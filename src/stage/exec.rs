//! Runs a stage as a state machine with these phases:
//!
//! - `Attempt`: the agent works on the stage and returns a report.
//! - `Gate`: the stage's gate checks the report and builds the stage's output.
//! - `Judge`: the judge reviews the output. A stage without a judge skips it.
//! - `Done`: the output passed.
//!
//! If the gate fails or the judge finds defects, the next attempt gets their
//! feedback as its prompt. All attempts run in the same agent session. The
//! execution stops with an error when the stage runs out of attempts or
//! budget, or when the agent stops the stage.
//!
//! [`StageExec::run`] calls a hook with the new state after every transition.
//! The state is serializable, so the hook can save it.

use std::{fmt, path::Path, time::Instant};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{
    agent::{Agent, AgentError},
    config::Config,
    log::{stage_debug, stage_info},
    stage::{GateStop, Stage},
    utils::format_elapsed,
};

/// The state of a stage's execution. Serializing it keeps the phase, not the
/// costs or the number of attempts.
#[derive(Serialize, Deserialize)]
#[serde(bound(deserialize = ""))]
pub struct StageExec<S: Stage> {
    stage: S,
    session: Option<Uuid>,
    phase: Phase<S>,

    #[serde(skip)]
    worker_cost: f64,

    #[serde(skip)]
    judge_cost: f64,

    #[serde(skip)]
    attempts: usize,
}

#[derive(Serialize, Deserialize)]
enum Phase<S: Stage> {
    Attempt {
        prompt: String,
    },
    Gate {
        report: S::Report,
    },
    Judge {
        report: S::Report,
        output: S::Output,
    },
    Done(S::Output),
}

impl<S: Stage> StageExec<S> {
    /// Returns the execution of `stage`. The first attempt gets the stage's
    /// base prompt.
    pub fn new(stage: S) -> Self {
        let prompt = stage.base_prompt();
        Self {
            stage,
            session: None,
            attempts: 0,
            phase: Phase::Attempt { prompt },
            worker_cost: 0f64,
            judge_cost: 0f64,
        }
    }

    /// Runs the stage until it is done and returns its output. Calls
    /// `on_change` with the new state after every transition.
    pub fn run(
        mut self,
        cfg: &Config,
        workdir: &Path,
        mut on_change: impl FnMut(&Self),
    ) -> Result<S::Output, StageExecutionError<S>> {
        let mut agent = self.init_agent(cfg, workdir)?;
        loop {
            let next = match &self.phase {
                Phase::Attempt { prompt } => {
                    match self.run_attempt(cfg, &mut agent, prompt.clone())? {
                        Ok(report) => {
                            self.attempts += 1;
                            // the worker agent is kept alive over the whole run
                            // and claude code accumulated the usage over all
                            // calls. So here we
                            // override instead of adding.
                            self.worker_cost = agent.get_usage().cost_usd;
                            Phase::Gate { report }
                        }
                        Err(blocker) => {
                            log_stage_spent::<S>(cfg, agent.get_usage().cost_usd, self.judge_cost);
                            return Err(StageExecutionError::Blocked(blocker));
                        }
                    }
                }
                Phase::Gate { report } => {
                    match self.stage.gate_check(report) {
                        Ok(o) => {
                            stage_info!(S::NAME, "gate passed");
                            Phase::Judge {
                                report: report.clone(),
                                output: o.clone(),
                            }
                        }
                        Err(GateStop::Failure(failure)) => {
                            stage_info!(S::NAME, "Gate failed: {}", failure);
                            // get the feedback prompt
                            self.retry(cfg, AttemptRejected::Gate(failure.clone()))?
                        }
                        Err(GateStop::Error(e)) => {
                            return Err(StageExecutionError::GateExecutionError(e));
                        }
                    }
                }
                Phase::Judge { report, output } => {
                    if let Some(prompt) = self.stage.judge_prompt(output, report) {
                        if self.worker_cost + self.judge_cost >= cfg.stage_max_budget {
                            return Err(StageExecutionError::BudgetExhausted(None));
                        }
                        let (defects, cost_usd) = self.run_judge(prompt, cfg, workdir)?;
                        self.judge_cost += cost_usd;
                        if defects.is_empty() {
                            stage_info!(S::NAME, "judge found no defects, continuing");
                            log_stage_spent::<S>(cfg, self.worker_cost, self.judge_cost);
                            Phase::Done(output.clone())
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
                    } else {
                        stage_info!(S::NAME, "stage has no judge, continuing");
                        log_stage_spent::<S>(cfg, self.worker_cost, self.judge_cost);
                        Phase::Done(output.clone())
                    }
                }
                Phase::Done(output) => return Ok(output.clone()),
            };
            self.phase = next;
            on_change(&self);
        }
    }

    fn init_agent(&mut self, cfg: &Config, dir: &Path) -> Result<Agent, StageExecutionError<S>> {
        let mut agent = match self.session {
            Some(session_id) => Agent::with_session_id(session_id),
            None => Agent::default(),
        };
        // set the briefing
        agent.set_briefing(self.stage.briefing());
        for (name, content) in self.stage.skills() {
            agent.add_skill(name, content);
        }
        agent.set_effort(cfg.effort);
        agent.set_model(cfg.model.clone());
        agent.set_dir(dir.to_owned());
        agent.set_json_schema(
            json_schema::<AgentResult<S::Report, S::Blocker>>().map_err(AgentError::from)?,
        );
        Ok(agent)
    }

    /// Returns the agent's report, or the blocker it stopped the stage with.
    fn run_attempt(
        &mut self,
        cfg: &Config,
        agent: &mut Agent,
        prompt: String,
    ) -> Result<Result<S::Report, S::Blocker>, AgentError> {
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
        agent.set_max_budget(remaining);
        let t = Instant::now();
        let output = agent.run_with_hook(prompt, |s| stage_info!(S::NAME, "agent: {}", s))?;
        stage_info!(S::NAME, "agent finished in {}", format_elapsed(t));

        let result: AgentResult<S::Report, S::Blocker> = serde_json::from_str(&output)?;
        if let Ok(report) = &result.result {
            stage_debug!(
                S::NAME,
                "agent report: {}",
                serde_json::to_string_pretty(report)?
            );
        }
        Ok(result.result)
    }

    fn run_judge(
        &self,
        prompt: String,
        cfg: &Config,
        workdir: &Path,
    ) -> Result<(Vec<Defect>, f64), AgentError> {
        let mut judge_agent = Agent::default();
        judge_agent.set_effort(cfg.effort);
        judge_agent.set_model(cfg.model.clone());
        judge_agent.set_dir(workdir.to_owned());
        judge_agent.set_json_schema(json_schema::<Verdict>()?);
        let remaining = cfg.stage_max_budget - self.worker_cost - self.judge_cost;
        judge_agent.set_max_budget(remaining);

        stage_info!(
            S::NAME,
            "running judge, remaining budget: {:.2}/{} USD (agent spent: {:.2} USD, judge spent: {:.2} USD)",
            remaining,
            cfg.stage_max_budget,
            self.worker_cost,
            self.judge_cost
        );
        let t = Instant::now();
        let judge_output =
            judge_agent.run_with_hook(prompt, |s| stage_info!(S::NAME, "judge: {}", s))?;
        stage_info!(S::NAME, "judge finished in {}", format_elapsed(t));
        let verdict: Verdict = serde_json::from_str(&judge_output)?;
        Ok((verdict.defects, judge_agent.get_usage().cost_usd))
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
            "\nIn your report, list every change that you made since the start of the stage.\n",
        );
        Ok(Phase::Attempt { prompt })
    }
}

/// Why the execution of a stage stopped without an output.
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

/// A defect the judge found in a stage's output.
#[derive(Debug, Serialize, Deserialize, JsonSchema, Clone, PartialEq, Eq)]
pub struct Defect {
    pub kind: String,
    pub detail: String,
    pub fix: String,
    pub theorem: Option<String>,
}

impl fmt::Display for Defect {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind, self.detail)
    }
}

// The agent's structured output: its report, or the reason why it stopped
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
    use super::*;
    use crate::stage::{
        Never,
        pre_translate::PreTranslateReport,
        translate::{TranslateBlocker, TranslateReport},
    };

    type TranslateResult = AgentResult<TranslateReport, TranslateBlocker>;
    type PreTranslateResult = AgentResult<PreTranslateReport, Never>;

    #[test]
    fn agent_result_schemas_are_draft_07_objects() {
        for schema in [
            json_schema::<TranslateResult>().unwrap(),
            json_schema::<PreTranslateResult>().unwrap(),
        ] {
            let schema: serde_json::Value = serde_json::from_str(&schema).unwrap();
            assert_eq!(schema["$schema"], "http://json-schema.org/draft-07/schema#");
            assert_eq!(schema["type"], "object");
        }
    }

    #[test]
    fn agent_result_without_blocker_parses_a_report() {
        let result: PreTranslateResult =
            serde_json::from_str(r#"{"result": {"Ok": {"changes": []}}}"#).unwrap();
        assert!(result.result.is_ok());
    }

    #[test]
    fn agent_result_parses_a_report_and_a_blocker() {
        let report: TranslateResult = serde_json::from_str(
            r#"{"result": {"Ok": {
                "extract_changes": [], "rust_changes": [], "lean_definitions": [],
                "kept_axioms": [], "unmatched_opaque_patterns": [], "notes": []
            }}}"#,
        )
        .unwrap();
        assert!(report.result.is_ok());

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
}
