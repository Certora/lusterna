//! Runs a stage as a state machine with these phases:
//!
//! - `Attempt`: the agent works on the stage and returns a submission.
//! - `Gate`: the stage's gate checks the submission.
//! - `Judge`: the judge reviews the submission. A stage without a judge skips
//!   it.
//! - `Done`: the submission passed.
//!
//! If the gate fails or the judge finds defects, the next attempt gets their
//! feedback as its prompt. All attempts run in the same agent session. The
//! execution stops with an error when the stage runs out of attempts or
//! budget, or when the agent stops the stage.
//!
//!
//! [`StageExec::run`] calls a hook with the new state after every transition.
//! The state is serializable, so the hook can save it.
//!
//! ## Design of the Judge
//! The Judge is supposed to be an adversarial agent that finds defects in the
//! worker's output. The best practice would be to spawn a fresh judge after
//! each attempt. However, testing on larger codebases showed that this
//! can lead to the worker and judge never converging: after each attempt, the
//! new judge would flag completely different defects than the previous judges
//! (and almost all of them benign). Additionally, there was no guarantee that
//! the fixes of previous defects were actually re-evaluated.
//!
//! To work around this, the Judge is kept active (i.e. resumed after the first
//! invocation), so it keeps its context and is only instructed to check if the
//! initially found defects were fixed and whether these fixes introduced other
//! defects.

use std::{
    fmt, fs, io,
    path::{Path, PathBuf},
    time::Instant,
};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize, de::DeserializeOwned};

use crate::{
    agent::{Agent, AgentError, Session, claude::ClaudeCode},
    config::Config,
    log::{stage_debug, stage_info},
    stage::Stage,
    utils::format_elapsed,
};

/// Added to the system prompt of the agent and the judge.
const WAIT_FOR_SUBAGENTS: &str = "Before you submit your structured output, wait until every subagent you started has finished and use its results. Results that arrive after the submission are lost.";

/// The judge's prompt after its first verdict.
const RECHECK_PROMPT: &str = "The agent changed its work to fix the defects of your last verdict. Do not review the whole work again. For each defect, check whether the agent fixed it, and review the code that the agent changed to fix it. Report a defect again only if it is not fixed. Do not report a defect again if it turns out to be wrong. Report a new defect only in the code that the agent changed to fix these defects.";

/// Added to the base prompt of a stage that `StageExec::restart` starts
/// again.
const EARLIER_ATTEMPT: &str = "The work tree contains the uncommitted work of an earlier attempt at this stage. Review it first, keep what is correct and continue from it. Your submission must cover the whole work of the stage, including what you keep.";

/// The state of a stage's execution. Serializing it keeps the phase and the
/// costs, not the number of attempts.
#[derive(Serialize, Deserialize)]
#[serde(bound(deserialize = "A: Deserialize<'de>"))]
pub struct StageExec<S: Stage, A: Agent = ClaudeCode> {
    stage: S,

    worker_session: Session<A>,
    judge_session: Session<A>,

    phase: Phase<S>,

    worker_cost: f64,

    judge_cost: f64,

    #[serde(skip)]
    attempts: usize,
}

#[derive(Serialize, Deserialize)]
enum Phase<S: Stage> {
    Attempt { prompt: String },
    Gate { submission: S::Submission },
    Judge { submission: S::Submission },
    Done(S::Submission),
}

impl<S: Stage> StageExec<S> {
    /// Returns the execution of `stage`. The first attempt gets the stage's
    /// base prompt.
    pub fn new(stage: S, cfg: &Config) -> Result<Self, StageExecutionError<S>> {
        let worker = Self::get_worker_session(&stage, cfg)?;
        let judge = Self::get_judge_session(cfg)?;
        Ok(Self::with_sessions(stage, worker, judge))
    }
}

impl<S: Stage, A: Agent + DeserializeOwned> StageExec<S, A> {
    /// Returns the execution that was serialized to `path`, ready to continue
    /// after a run that stopped in the middle of a phase.
    pub fn load(path: &Path, cfg: &Config) -> Result<Self, StageExecutionError<S>> {
        let state_error = |e: io::Error| StageExecutionError::StateFile(path.to_path_buf(), e);
        let text = fs::read_to_string(path).map_err(state_error)?;
        let mut exec: Self = serde_json::from_str(&text).map_err(|e| state_error(e.into()))?;

        match &exec.phase {
            // The attempt resumes the session of the worker and gets the same
            // prompt again.
            Phase::Attempt { .. } if exec.worker_session.get_session_id().is_some() => {
                exec.worker_session.set_started()
            }
            // The id of a judge session that is not started may be in use by
            // a judge run that did not return, so the judge gets a new
            // session.
            Phase::Judge { .. } if !exec.judge_session.started() => {
                exec.judge_session = Self::get_judge_session(cfg)?
            }
            _ => {}
        }
        Ok(exec)
    }
}

impl<S: Stage, A: Agent> StageExec<S, A> {
    fn get_worker_session(stage: &S, cfg: &Config) -> Result<Session<A>, AgentError> {
        let mut sessions = Session::<A>::default();
        sessions.set_briefing(format!("{}\n\n{WAIT_FOR_SUBAGENTS}", stage.briefing()));
        sessions.set_options(stage.agent_options(cfg));
        for (name, content) in stage.skills() {
            sessions.add_skill(name, content);
        }
        sessions.set_effort(cfg.effort);
        sessions.set_model(cfg.model.clone());
        sessions.set_dir(cfg.workspace_root()?);

        Ok(sessions)
    }

    fn get_judge_session(cfg: &Config) -> Result<Session<A>, AgentError> {
        let mut sessions = Session::<A>::default();

        sessions.set_briefing(WAIT_FOR_SUBAGENTS.to_owned());
        sessions.set_effort(cfg.effort);
        sessions.set_model(cfg.model.clone());
        sessions.set_dir(cfg.workspace_root()?);

        Ok(sessions)
    }

    pub fn with_sessions(
        stage: S,
        worker_sessions: Session<A>,
        judge_sessions: Session<A>,
    ) -> Self {
        let prompt = stage.base_prompt();
        Self {
            stage,
            worker_session: worker_sessions,
            judge_session: judge_sessions,
            attempts: 0,
            phase: Phase::Attempt { prompt },
            worker_cost: 0f64,
            judge_cost: 0f64,
        }
    }

    /// Returns the execution that runs the stage of `self` again: with new
    /// sessions, without costs, and with the stage's base prompt and
    /// `EARLIER_ATTEMPT` as the prompt of the first attempt. The worker
    /// session has no id, so that `load` does not resume it.
    pub fn restart(self, cfg: &Config) -> Result<Self, StageExecutionError<S>> {
        let mut worker = Self::get_worker_session(&self.stage, cfg)?;
        worker.clear_session_id();
        let judge = Self::get_judge_session(cfg)?;
        let prompt = format!("{}\n\n{EARLIER_ATTEMPT}", self.stage.base_prompt());
        Ok(Self {
            stage: self.stage,
            worker_session: worker,
            judge_session: judge,
            attempts: 0,
            phase: Phase::Attempt { prompt },
            worker_cost: 0f64,
            judge_cost: 0f64,
        })
    }

    /// Runs the stage until it is done and returns the submission that passed.
    /// Calls `on_change` with the new state after every transition.
    pub fn run(
        mut self,
        cfg: &Config,
        mut on_change: Option<&mut dyn FnMut(&Self)>,
    ) -> Result<S::Submission, StageExecutionError<S>> {
        loop {
            let next = match &self.phase {
                Phase::Attempt { prompt } => {
                    let (res, usage) = self.run_attempt(cfg, prompt.clone())?;
                    self.worker_cost = usage;
                    log_stage_spent::<S>(cfg, usage, self.judge_cost);

                    match res {
                        Ok(submission) => {
                            self.attempts += 1;

                            Phase::Gate { submission }
                        }
                        Err(blocker) => {
                            return Err(StageExecutionError::Blocked(blocker));
                        }
                    }
                }
                Phase::Gate { submission } => {
                    match self
                        .stage
                        .gate_check(submission)
                        .map_err(StageExecutionError::GateExecutionError)?
                    {
                        Some(failure) => {
                            stage_info!(S::NAME, "Gate failed: {}", failure);
                            // get the feedback prompt
                            self.retry(cfg, AttemptRejected::Gate(failure.clone()))?
                        }
                        None => {
                            stage_info!(S::NAME, "gate passed");
                            Phase::Judge {
                                submission: submission.clone(),
                            }
                        }
                    }
                }
                Phase::Judge { submission } => {
                    // need to clone these to circumvent borrow errors
                    let submission = submission.clone();

                    if self.worker_cost + self.judge_cost >= cfg.stage_max_budget {
                        return Err(StageExecutionError::BudgetExhausted(None));
                    }
                    let (defects, cost_usd) = self.run_judge(&submission, cfg)?;
                    self.judge_cost = cost_usd;

                    if defects.is_empty() {
                        stage_info!(S::NAME, "judge found no defects, continuing");
                        log_stage_spent::<S>(cfg, self.worker_cost, self.judge_cost);
                        Phase::Done(submission)
                    } else {
                        stage_info!(S::NAME, "judge found defects");
                        stage_debug!(
                            S::NAME,
                            "defects:\n{}",
                            defects
                                .iter()
                                .map(|d| d.to_string())
                                .collect::<Vec<String>>()
                                .join("\n")
                        );
                        self.retry(cfg, AttemptRejected::Judge(defects))?
                    }
                }
                Phase::Done(submission) => return Ok(submission.clone()),
            };
            self.phase = next;
            if let Some(on_change) = &mut on_change {
                on_change(&self);
            }
        }
    }

    /// Returns the agent's submission, or the blocker it stopped the stage
    /// with.
    #[allow(clippy::type_complexity)]
    fn run_attempt(
        &mut self,
        cfg: &Config,
        prompt: String,
    ) -> Result<(Result<S::Submission, S::Blocker>, f64), AgentError> {
        let remaining = cfg.stage_max_budget - self.worker_cost - self.judge_cost;

        stage_info!(
            S::NAME,
            "starting attempt {}/{}, remaining budget: {:.2}/{} USD (agent spent: {:.2} USD, judge spent: {:.2} USD)",
            self.attempts + 1,
            cfg.stage_max_attempts,
            remaining,
            cfg.stage_max_budget,
            self.worker_cost,
            self.judge_cost
        );

        let session = &mut self.worker_session;
        session.set_max_budget(remaining);

        let schema =
            json_schema::<AgentResult<S::Submission, S::Blocker>>().map_err(AgentError::from)?;
        let t = Instant::now();
        let response = session.run(prompt, Some(schema), |s| {
            stage_info!(S::NAME, "agent: {}", s)
        })?;
        stage_info!(S::NAME, "agent finished in {}", format_elapsed(t));

        let result: AgentResult<S::Submission, S::Blocker> =
            serde_json::from_str(&response.output)?;
        if let Ok(submission) = &result.result {
            stage_debug!(
                S::NAME,
                "agent submission: {}",
                serde_json::to_string_pretty(submission)?
            );
        }

        // the worker agent is kept alive over the whole run
        // and claude code accumulated the usage over all
        // calls. So here we
        // override instead of adding.
        let usage_cost = session.get_usage().cost_usd;

        Ok((result.result, usage_cost))
    }

    fn run_judge(
        &mut self,
        submission: &S::Submission,
        cfg: &Config,
    ) -> Result<(Vec<Defect>, f64), AgentError> {
        let remaining = cfg.stage_max_budget - self.worker_cost - self.judge_cost;

        let Some(base_prompt) = self.stage.judge_prompt(submission) else {
            stage_info!(S::NAME, "stage has no judge, continuing");
            return Ok((vec![], 0.0));
        };

        stage_info!(
            S::NAME,
            "running judge, remaining budget: {:.2}/{} USD (agent spent: {:.2} USD, judge spent: {:.2} USD)",
            remaining,
            cfg.stage_max_budget,
            self.worker_cost,
            self.judge_cost
        );

        let judge_session = &mut self.judge_session;

        // Check if we are running a second judge attempt.
        // In that case, shift focus to the defects flagged in previous round.
        // If we don't do that, chances are the two agents never converge to a
        // passing version.
        let prompt = if judge_session.started() {
            format!(
                "{RECHECK_PROMPT}\n\nThe agent's submission:\n{}",
                serde_json::to_string_pretty(submission)?
            )
        } else {
            base_prompt
        };

        judge_session.set_max_budget(remaining);

        let schema = json_schema::<Verdict>()?;

        let t = Instant::now();
        let judge_response = judge_session.run(prompt, Some(schema), |s| {
            stage_info!(S::NAME, "judge: {}", s)
        })?;
        stage_info!(S::NAME, "judge finished in {}", format_elapsed(t));
        let verdict: Verdict = serde_json::from_str(&judge_response.output)?;
        Ok((verdict.defects, judge_session.get_usage().cost_usd))
    }

    fn retry(
        &self,
        cfg: &Config,
        rejection: AttemptRejected<S>,
    ) -> Result<Phase<S>, StageExecutionError<S>> {
        if self.attempts >= cfg.stage_max_attempts {
            return Err(StageExecutionError::MaxRetries(Some(rejection)));
        }
        if self.judge_cost + self.worker_cost >= cfg.stage_max_budget {
            return Err(StageExecutionError::BudgetExhausted(Some(rejection)));
        }
        let mut prompt = match rejection {
            AttemptRejected::Gate(failure) => S::feedback_prompt(&failure),
            AttemptRejected::Judge(defects) => defects_feedback_prompt(&defects),
        };
        prompt.push_str(
            "\nIn your submission, list every change that you made since the start of the stage.\n",
        );
        Ok(Phase::Attempt { prompt })
    }
}

/// Why the execution of a stage stopped without a submission that passed.
#[derive(thiserror::Error)]
pub enum StageExecutionError<S: Stage> {
    /// The stage's setup failed.
    #[error("setup failed: {0}")]
    SetupError(S::SetupError),
    /// The gate could not run its checks.
    #[error("failed to execute the gate: {0}")]
    GateExecutionError(S::GateExecutionError),
    /// The stage used all its attempts. Holds why the last attempt was
    /// rejected.
    #[error("max attempts reached{}", .0.as_ref().map(|r| format!(", {r}")).unwrap_or_default())]
    MaxRetries(Option<AttemptRejected<S>>),
    /// The stage used its budget. Holds why the last attempt was rejected if
    /// the budget ran out between attempts.
    #[error("budget exhausted{}", .0.as_ref().map(|r| format!(", {r}")).unwrap_or_default())]
    BudgetExhausted(Option<AttemptRejected<S>>),
    /// The agent stopped the stage.
    #[error("the agent stopped the stage: {0}")]
    Blocked(S::Blocker),
    /// Running the agent or the judge failed.
    #[error(transparent)]
    AgentError(AgentError),
    /// The file that holds the state of the execution cannot be read or
    /// parsed.
    #[error("cannot load the stage state from `{0}`: {1}")]
    StateFile(PathBuf, io::Error),
}

impl<S: Stage> From<AgentError> for StageExecutionError<S> {
    fn from(e: AgentError) -> Self {
        match e {
            AgentError::BudgetExhausted => StageExecutionError::BudgetExhausted(None),
            e => StageExecutionError::AgentError(e),
        }
    }
}

impl<S: Stage> fmt::Debug for StageExecutionError<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(self, f)
    }
}

/// Why an attempt was rejected.
#[derive(Debug, Serialize, Deserialize, Clone, PartialEq, Eq)]
pub enum AttemptRejected<S: Stage> {
    /// The gate failed.
    Gate(S::GateFailure),
    /// The judge found these defects.
    Judge(Vec<Defect>),
}

impl<S: Stage> fmt::Display for AttemptRejected<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AttemptRejected::Gate(e) => write!(f, "last gate error: {e}"),
            AttemptRejected::Judge(defects) => {
                write!(f, "last judge defects:")?;
                for d in defects {
                    write!(f, "\n- {d}")?;
                }
                Ok(())
            }
        }
    }
}

/// A defect the judge found in a stage's submission.
#[derive(Debug, Serialize, Deserialize, JsonSchema, Clone, PartialEq, Eq)]
pub struct Defect {
    /// One of the defect kinds that the stage's judge prompt lists.
    pub kind: String,
    /// Where the defect is and what is wrong.
    pub detail: String,
    /// The change that the agent must make.
    pub fix: String,
    /// The full Lean name of the theorem that has the defect, if any.
    pub theorem: Option<String>,
}

impl fmt::Display for Defect {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind, self.detail)
    }
}

// The agent's structured output: its submission, or the reason why it stopped
// the stage. `--json-schema` requires an object at the top level, so the
// `Result` is in a field.
#[derive(Deserialize, JsonSchema)]
struct AgentResult<R, B> {
    result: Result<R, B>,
}

/// The judge's structured output. `--json-schema` requires an object at the
/// top level, so the defect list is wrapped in this struct. An empty `defects`
/// list approves the stage.
#[derive(Deserialize, JsonSchema)]
struct Verdict {
    defects: Vec<Defect>,
}

/// Returns the JSON schema of `T` for `claude --json-schema`, which only
/// accepts draft-07 schemas. `schema_for!` generates a draft 2020-12 schema,
/// which it rejects.
fn json_schema<T: JsonSchema>() -> Result<String, serde_json::Error> {
    let schema = schemars::generate::SchemaSettings::draft07()
        .into_generator()
        .into_root_schema_for::<T>();
    serde_json::to_string(&schema)
}

/// Returns the feedback prompt for the agent for the defects the judge found
fn defects_feedback_prompt(defects: &[Defect]) -> String {
    let mut prompt = String::from("The judge found these defects, fix them:\n");
    for d in defects {
        let who = d.theorem.as_deref().unwrap_or(&d.kind);
        prompt.push_str(&format!(
            "- {} [{}]: {} -> fix: {}\n",
            who, d.kind, d.detail, d.fix
        ));
    }
    prompt
}

/// Logs what the stage spent of its budget.
fn log_stage_spent<S: Stage>(cfg: &Config, agent_cost: f64, judge_cost: f64) {
    stage_info!(
        S::NAME,
        "stage spent {:.2}/{} USD (agent: {:.2} USD, judge: {:.2} USD)",
        agent_cost + judge_cost,
        cfg.stage_max_budget,
        agent_cost,
        judge_cost
    );
}

#[cfg(test)]
mod tests {
    use std::io;

    use serde_json::json;
    use tempfile::TempDir;

    use super::*;
    use crate::{
        agent::{ModelUsage, Response, RunError, mock::MockAgent},
        stage::{
            Never, StageName,
            formalize::FormalizeStage,
            infer::InferStage,
            pre_translate::{PreTranslateStage, PreTranslateSubmission},
            prove::ProveStage,
            translate::{TranslateBlocker, TranslateStage, TranslateSubmission},
        },
        utils::parse_config,
    };

    type TranslateResult = AgentResult<TranslateSubmission, TranslateBlocker>;
    type PreTranslateResult = AgentResult<PreTranslateSubmission, Never>;

    #[test]
    fn structured_output_schemas_are_draft_07_objects() {
        assert_draft_07_object::<StageResult<InferStage>>();
        assert_draft_07_object::<StageResult<PreTranslateStage>>();
        assert_draft_07_object::<StageResult<TranslateStage>>();
        assert_draft_07_object::<StageResult<FormalizeStage>>();
        assert_draft_07_object::<StageResult<ProveStage>>();
        assert_draft_07_object::<Verdict>();
    }

    type StageResult<S> = AgentResult<<S as Stage>::Submission, <S as Stage>::Blocker>;

    fn assert_draft_07_object<T: JsonSchema>() {
        let schema: serde_json::Value = serde_json::from_str(&json_schema::<T>().unwrap()).unwrap();
        assert_eq!(schema["$schema"], "http://json-schema.org/draft-07/schema#");
        assert_eq!(schema["type"], "object");
    }

    #[test]
    fn agent_result_without_blocker_parses_a_submission() {
        let result: PreTranslateResult =
            serde_json::from_str(r#"{"result": {"Ok": {"changes": []}}}"#).unwrap();
        assert!(result.result.is_ok());
    }

    #[test]
    fn agent_result_parses_a_submission_and_a_blocker() {
        let submission: TranslateResult = serde_json::from_str(
            r#"{"result": {"Ok": {
                "extract_changes": [], "rust_changes": [], "lean_definitions": [],
                "kept_axioms": [], "unmatched_opaque_patterns": [], "notes": []
            }}}"#,
        )
        .unwrap();
        assert!(submission.result.is_ok());

        let blocker: TranslateResult = serde_json::from_str(
            r#"{"result": {"Err": {"missing_stub_item": {
                "crate": "uint", "item": "From<i32>", "used_by": "decimal-wad src/rate.rs"
            }}}}"#,
        )
        .unwrap();
        assert!(matches!(
            blocker.result,
            Err(TranslateBlocker::MissingStubItem { .. })
        ));
    }

    /// The line that `StageExec::retry` appends to each feedback prompt
    const LIST_CHANGES: &str =
        "\nIn your submission, list every change that you made since the start of the stage.\n";

    /// The result of the gate check that a `TestSubmission` asks for
    #[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
    enum Gate {
        Pass,
        Fail,
        Error,
    }

    #[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
    struct TestSubmission {
        gate: Gate,
    }

    /// A stage whose gate returns what the submission asks for, and that has
    /// a judge if `judge` is set
    #[derive(Serialize, Deserialize)]
    struct MockStage {
        judge: bool,
    }

    impl Stage for MockStage {
        const NAME: StageName = StageName::Translate;
        type Input = ();
        type Submission = TestSubmission;
        type Blocker = String;
        type GateFailure = String;
        type GateExecutionError = io::Error;
        type SetupError = io::Error;

        fn setup(_input: ()) -> Result<Self, io::Error> {
            Ok(MockStage { judge: true })
        }

        fn base_prompt(&self) -> String {
            "base prompt".to_owned()
        }

        fn briefing(&self) -> String {
            "briefing".to_owned()
        }

        fn skills(&self) -> Vec<(&'static str, String)> {
            vec![("skill", "content".to_owned())]
        }

        fn feedback_prompt(failure: &String) -> String {
            format!("fix: {failure}")
        }

        fn gate_check(&self, submission: &TestSubmission) -> Result<Option<String>, io::Error> {
            match submission.gate {
                Gate::Pass => Ok(None),
                Gate::Fail => Ok(Some("gate failed".to_owned())),
                Gate::Error => Err(io::Error::other("gate error")),
            }
        }

        fn judge_prompt(&self, _submission: &TestSubmission) -> Option<String> {
            self.judge.then(|| "judge prompt".to_owned())
        }
    }

    struct Fixture {
        exec: StageExec<MockStage, MockAgent>,
        worker: MockAgent,
        judge: MockAgent,
        cfg: Config,
        _workspace: TempDir,
    }

    fn fixture(judge: bool, max_attempts: usize, max_budget: f64) -> Fixture {
        let workspace = tempfile::tempdir().unwrap();
        let cfg = parse_config([
            "--workspace-root".to_owned(),
            workspace.path().display().to_string(),
            "--stage-max-attempts".to_owned(),
            max_attempts.to_string(),
            "--stage-max-budget".to_owned(),
            max_budget.to_string(),
        ]);
        let stage = MockStage { judge };
        let worker_session =
            StageExec::<MockStage, MockAgent>::get_worker_session(&stage, &cfg).unwrap();
        let judge_session = StageExec::<MockStage, MockAgent>::get_judge_session(&cfg).unwrap();
        Fixture {
            worker: worker_session.agent().clone(),
            judge: judge_session.agent().clone(),
            exec: StageExec::with_sessions(stage, worker_session, judge_session),
            cfg,
            _workspace: workspace,
        }
    }

    fn response(
        session_id: &str,
        output: serde_json::Value,
        cost_usd: f64,
    ) -> Result<Response, RunError> {
        Ok(Response {
            session_id: session_id.to_owned(),
            output: output.to_string(),
            usage: ModelUsage {
                cost_usd,
                ..ModelUsage::default()
            },
        })
    }

    fn submission(gate: Gate, cost_usd: f64) -> Result<Response, RunError> {
        response(
            "worker",
            json!({"result": {"Ok": {"gate": gate}}}),
            cost_usd,
        )
    }

    fn verdict(defects: &[Defect], cost_usd: f64) -> Result<Response, RunError> {
        response("judge", json!({ "defects": defects }), cost_usd)
    }

    fn defect() -> Defect {
        Defect {
            kind: "kind".to_owned(),
            detail: "detail".to_owned(),
            fix: "fix".to_owned(),
            theorem: None,
        }
    }

    fn prompts(agent: &MockAgent) -> Vec<String> {
        agent.requests().iter().map(|r| r.prompt.clone()).collect()
    }

    fn budgets(agent: &MockAgent) -> Vec<Option<f64>> {
        agent
            .requests()
            .iter()
            .map(|r| r.budget_limit_usd)
            .collect()
    }

    #[test]
    fn passing_gate_and_verdict_finish_the_stage() {
        let f = fixture(true, 3, 10.0);
        f.worker.push(submission(Gate::Pass, 1.0));
        f.judge.push(verdict(&[], 0.5));

        let result = f.exec.run(&f.cfg, None).unwrap();
        assert_eq!(result, TestSubmission { gate: Gate::Pass });
        assert_eq!(prompts(&f.worker), ["base prompt"]);
        assert_eq!(prompts(&f.judge), ["judge prompt"]);
    }

    #[test]
    fn sessions_get_the_settings_of_the_stage() {
        let f = fixture(true, 3, 10.0);
        f.worker.push(submission(Gate::Pass, 1.0));
        f.judge.push(verdict(&[], 0.5));
        f.exec.run(&f.cfg, None).unwrap();

        let root = f.cfg.workspace_root().unwrap();
        let worker = f.worker.requests();
        let briefing = format!("briefing\n\n{WAIT_FOR_SUBAGENTS}");
        assert_eq!(worker[0].briefing.as_deref(), Some(briefing.as_str()));
        assert_eq!(
            worker[0].skills,
            [("skill".to_owned(), "content".to_owned())]
        );
        assert_eq!(worker[0].workdir.as_deref(), Some(root.as_path()));

        let judge = f.judge.requests();
        assert_eq!(judge[0].briefing.as_deref(), Some(WAIT_FOR_SUBAGENTS));
        assert!(judge[0].skills.is_empty());
        assert_eq!(judge[0].workdir.as_deref(), Some(root.as_path()));
    }

    #[test]
    fn stage_without_judge_skips_the_judge() {
        let f = fixture(false, 3, 10.0);
        f.worker.push(submission(Gate::Pass, 1.0));

        f.exec.run(&f.cfg, None).unwrap();
        assert!(f.judge.requests().is_empty());
    }

    #[test]
    fn failed_gate_retries_in_the_same_session_with_its_feedback() {
        let f = fixture(false, 3, 10.0);
        f.worker.push(submission(Gate::Fail, 1.0));
        f.worker.push(submission(Gate::Pass, 2.0));
        f.exec.run(&f.cfg, None).unwrap();

        let worker = f.worker.requests();
        assert_eq!(worker[1].prompt, format!("fix: gate failed{LIST_CHANGES}"));
        assert_eq!(worker[1].session_id.as_deref(), Some("worker"));
    }

    #[test]
    fn judge_defects_go_to_the_worker_and_the_judge_rechecks_them() {
        let f = fixture(true, 3, 10.0);
        f.worker.push(submission(Gate::Pass, 1.0));
        f.judge.push(verdict(&[defect()], 1.0));
        f.worker.push(submission(Gate::Pass, 2.0));
        f.judge.push(verdict(&[], 2.0));
        f.exec.run(&f.cfg, None).unwrap();

        let worker = f.worker.requests();
        let feedback = defects_feedback_prompt(&[defect()]);
        assert_eq!(worker[1].prompt, format!("{feedback}{LIST_CHANGES}"));
        assert_eq!(worker[1].session_id.as_deref(), Some("worker"));

        let judge = f.judge.requests();
        assert!(judge[1].prompt.starts_with(RECHECK_PROMPT));
        assert_eq!(judge[1].session_id.as_deref(), Some("judge"));
    }

    #[test]
    fn last_attempt_stops_the_stage_with_its_rejection() {
        let f = fixture(false, 2, 10.0);
        f.worker.push(submission(Gate::Fail, 1.0));
        f.worker.push(submission(Gate::Fail, 2.0));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(
            error,
            StageExecutionError::MaxRetries(Some(AttemptRejected::Gate(failure)))
                if failure == "gate failed"
        ));
        assert_eq!(f.worker.requests().len(), 2);
    }

    #[test]
    fn budget_spent_by_a_rejected_attempt_stops_the_stage_with_its_rejection() {
        let f = fixture(false, 3, 1.0);
        f.worker.push(submission(Gate::Fail, 1.0));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(
            error,
            StageExecutionError::BudgetExhausted(Some(AttemptRejected::Gate(_)))
        ));
    }

    #[test]
    fn budget_spent_before_the_judge_stops_the_stage() {
        let f = fixture(true, 3, 1.0);
        f.worker.push(submission(Gate::Pass, 1.0));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(error, StageExecutionError::BudgetExhausted(None)));
        assert!(f.judge.requests().is_empty());
    }

    #[test]
    fn agent_that_exhausts_its_budget_stops_the_stage() {
        let f = fixture(false, 3, 10.0);
        f.worker.push(Err(RunError {
            session_id: Some("worker".to_owned()),
            usage: None,
            kind: AgentError::BudgetExhausted,
        }));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(error, StageExecutionError::BudgetExhausted(None)));
    }

    #[test]
    fn each_run_gets_the_remaining_budget() {
        let f = fixture(true, 3, 10.0);
        f.worker.push(submission(Gate::Pass, 2.0));
        f.judge.push(verdict(&[defect()], 1.0));
        // A resumed session reports the cost of all its runs.
        f.worker.push(submission(Gate::Pass, 5.0));
        f.judge.push(verdict(&[], 1.5));
        f.exec.run(&f.cfg, None).unwrap();

        assert_eq!(budgets(&f.worker), [Some(10.0), Some(7.0)]);
        assert_eq!(budgets(&f.judge), [Some(8.0), Some(4.0)]);
    }

    #[test]
    fn blocker_stops_the_stage() {
        let f = fixture(false, 3, 10.0);
        f.worker
            .push(response("worker", json!({"result": {"Err": "stuck"}}), 1.0));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(error, StageExecutionError::Blocked(blocker) if blocker == "stuck"));
    }

    #[test]
    fn gate_that_cannot_run_stops_the_stage() {
        let f = fixture(false, 3, 10.0);
        f.worker.push(submission(Gate::Error, 1.0));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(error, StageExecutionError::GateExecutionError(_)));
    }

    #[test]
    fn submission_that_does_not_match_the_schema_stops_the_stage() {
        let f = fixture(false, 3, 10.0);
        f.worker.push(response("worker", json!({"result": 1}), 1.0));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(
            error,
            StageExecutionError::AgentError(AgentError::Json(_))
        ));
    }

    #[test]
    fn verdict_that_does_not_match_the_schema_stops_the_stage() {
        let f = fixture(true, 3, 10.0);
        f.worker.push(submission(Gate::Pass, 1.0));
        f.judge.push(response("judge", json!({"defects": 1}), 1.0));

        let error = f.exec.run(&f.cfg, None).unwrap_err();
        assert!(matches!(
            error,
            StageExecutionError::AgentError(AgentError::Json(_))
        ));
    }

    #[test]
    fn started_judge_session_gets_the_recheck_prompt() {
        let mut f = fixture(true, 3, 10.0);
        f.exec.judge_session.set_started();
        f.worker.push(submission(Gate::Pass, 1.0));
        f.judge.push(verdict(&[], 0.5));
        f.exec.run(&f.cfg, None).unwrap();

        assert!(f.judge.requests()[0].prompt.starts_with(RECHECK_PROMPT));
    }

    /// An execution of `MockStage` with `ClaudeCode` sessions in `phase`,
    /// which serializes, unlike one with `MockAgent`
    fn claude_exec(cfg: &Config, phase: Phase<MockStage>) -> StageExec<MockStage> {
        let mut exec = StageExec::new(MockStage { judge: true }, cfg).unwrap();
        exec.phase = phase;
        exec
    }

    /// Writes the state `state` to a file in `dir` and loads it
    fn load(
        state: &impl Serialize,
        dir: &Path,
        cfg: &Config,
    ) -> Result<StageExec<MockStage>, StageExecutionError<MockStage>> {
        let path = dir.join("state.json");
        fs::write(&path, serde_json::to_string(state).unwrap()).unwrap();
        StageExec::load(&path, cfg)
    }

    fn session_ids(exec: &StageExec<MockStage>) -> [Option<String>; 2] {
        [
            exec.worker_session.get_session_id().cloned(),
            exec.judge_session.get_session_id().cloned(),
        ]
    }

    fn test_submission() -> TestSubmission {
        TestSubmission { gate: Gate::Pass }
    }

    #[test]
    fn load_keeps_the_state() {
        let f = fixture(true, 3, 10.0);
        let mut exec = claude_exec(
            &f.cfg,
            Phase::Gate {
                submission: test_submission(),
            },
        );
        exec.worker_cost = 1.5;
        exec.judge_cost = 0.5;

        let loaded = load(&exec, f._workspace.path(), &f.cfg).unwrap();
        assert!(matches!(
            &loaded.phase,
            Phase::Gate { submission } if *submission == test_submission()
        ));
        assert_eq!(session_ids(&loaded), session_ids(&exec));
        assert!(!loaded.worker_session.started());
        assert!(!loaded.judge_session.started());
        assert_eq!(loaded.worker_cost, 1.5);
        assert_eq!(loaded.judge_cost, 0.5);
        assert!(loaded.stage.judge);
    }

    #[test]
    fn restart_starts_the_stage_again_with_new_sessions() {
        let f = fixture(true, 3, 10.0);
        let mut exec = claude_exec(&f.cfg, Phase::Done(test_submission()));
        exec.worker_session.set_started();
        exec.judge_session.set_started();
        exec.worker_cost = 1.5;
        exec.judge_cost = 0.5;
        let [_, old_judge] = session_ids(&exec);

        let restarted = exec.restart(&f.cfg).unwrap();
        let expected = format!("base prompt\n\n{EARLIER_ATTEMPT}");
        assert!(matches!(&restarted.phase, Phase::Attempt { prompt } if *prompt == expected));
        let [worker, judge] = session_ids(&restarted);
        assert_eq!(worker, None);
        assert_ne!(judge, old_judge);
        assert!(!restarted.worker_session.started());
        assert!(!restarted.judge_session.started());
        assert_eq!(restarted.worker_cost, 0.0);
        assert_eq!(restarted.judge_cost, 0.0);
        assert!(restarted.stage.judge);

        // `load` does not resume the worker session
        let loaded = load(&restarted, f._workspace.path(), &f.cfg).unwrap();
        assert!(!loaded.worker_session.started());
    }

    #[test]
    fn load_in_the_attempt_phase_resumes_the_worker_session() {
        let f = fixture(true, 3, 10.0);
        let exec = claude_exec(
            &f.cfg,
            Phase::Attempt {
                prompt: "feedback".to_owned(),
            },
        );

        let loaded = load(&exec, f._workspace.path(), &f.cfg).unwrap();
        assert!(matches!(&loaded.phase, Phase::Attempt { prompt } if prompt == "feedback"));
        assert_eq!(session_ids(&loaded), session_ids(&exec));
        assert!(loaded.worker_session.started());
        assert!(!loaded.judge_session.started());
    }

    #[test]
    fn load_in_the_attempt_phase_does_not_resume_a_worker_session_without_id() {
        let f = fixture(true, 3, 10.0);
        let exec = claude_exec(
            &f.cfg,
            Phase::Attempt {
                prompt: "base prompt".to_owned(),
            },
        );
        let mut state = serde_json::to_value(&exec).unwrap();
        state["worker_session"]["session_id"] = serde_json::Value::Null;

        let loaded = load(&state, f._workspace.path(), &f.cfg).unwrap();
        assert!(loaded.worker_session.get_session_id().is_none());
        assert!(!loaded.worker_session.started());
    }

    #[test]
    fn load_in_the_judge_phase_replaces_a_judge_session_that_is_not_started() {
        let f = fixture(true, 3, 10.0);
        let exec = claude_exec(
            &f.cfg,
            Phase::Judge {
                submission: test_submission(),
            },
        );

        let loaded = load(&exec, f._workspace.path(), &f.cfg).unwrap();
        let [worker, judge] = session_ids(&exec);
        let [loaded_worker, loaded_judge] = session_ids(&loaded);
        assert_eq!(loaded_worker, worker);
        assert!(loaded_judge.is_some());
        assert_ne!(loaded_judge, judge);
        assert!(!loaded.judge_session.started());
        assert!(!loaded.worker_session.started());
    }

    #[test]
    fn load_in_the_judge_phase_keeps_a_started_judge_session() {
        let f = fixture(true, 3, 10.0);
        let mut exec = claude_exec(
            &f.cfg,
            Phase::Judge {
                submission: test_submission(),
            },
        );
        exec.judge_session.set_started();

        let loaded = load(&exec, f._workspace.path(), &f.cfg).unwrap();
        assert_eq!(session_ids(&loaded), session_ids(&exec));
        assert!(loaded.judge_session.started());
    }

    #[test]
    fn load_of_a_missing_or_malformed_state_file_fails() {
        let f = fixture(true, 3, 10.0);
        let dir = f._workspace.path();

        let missing = StageExec::<MockStage>::load(&dir.join("missing.json"), &f.cfg);
        assert!(matches!(
            missing,
            Err(StageExecutionError::StateFile(path, e))
                if path == dir.join("missing.json") && e.kind() == io::ErrorKind::NotFound
        ));

        let malformed = load(&"not a state", dir, &f.cfg);
        assert!(matches!(
            malformed,
            Err(StageExecutionError::StateFile(_, e)) if e.kind() == io::ErrorKind::InvalidData
        ));
    }

    #[test]
    fn on_change_gets_each_new_phase() {
        let f = fixture(true, 3, 10.0);
        f.worker.push(submission(Gate::Fail, 1.0));
        f.worker.push(submission(Gate::Pass, 2.0));
        f.judge.push(verdict(&[], 1.0));

        let mut phases = Vec::new();
        let mut on_change = |exec: &StageExec<MockStage, MockAgent>| {
            phases.push(match exec.phase {
                Phase::Attempt { .. } => "Attempt",
                Phase::Gate { .. } => "Gate",
                Phase::Judge { .. } => "Judge",
                Phase::Done(_) => "Done",
            })
        };
        f.exec.run(&f.cfg, Some(&mut on_change)).unwrap();
        assert_eq!(phases, ["Gate", "Attempt", "Gate", "Judge", "Done"]);
    }
}
