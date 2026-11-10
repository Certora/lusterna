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

use std::{fmt, path::Path, time::Instant};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    agent::{Agent, AgentError},
    config::Config,
    log::{stage_debug, stage_info},
    stage::{GateStop, Stage},
    utils::format_elapsed,
};

/// Added to the system prompt of the agent and the judge.
const WAIT_FOR_SUBAGENTS: &str = "Before you submit your structured output, wait until every subagent you started has finished and use its results. Results that arrive after the submission are lost.";

/// The judge's prompt after its first verdict.
const RECHECK_PROMPT: &str = "The agent changed its work to fix the defects of your last verdict. Do not review the whole work again. For each defect, check whether the agent fixed it, and review the code that the agent changed to fix it. Report a defect again only if it is not fixed. Do not report a defect again if it turns out to be wrong. Report a new defect only in the code that the agent changed to fix these defects.";

/// The state of a stage's execution. Serializing it keeps the phase and the
/// costs, not the number of attempts.
#[derive(Serialize, Deserialize)]
#[serde(bound(deserialize = ""))]
pub struct StageExec<S: Stage> {
    stage: S,

    worker: Option<Agent>,
    judge: Option<Agent>,

    phase: Phase<S>,

    worker_cost: f64,

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
            worker: None,
            judge: None,
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
        mut on_change: Option<&mut dyn FnMut(&Self)>,
    ) -> Result<S::Output, StageExecutionError<S>> {
        loop {
            let next = match &self.phase {
                Phase::Attempt { prompt } => {
                    let (res, usage) = self.run_attempt(cfg, prompt.clone())?;
                    self.worker_cost = usage;
                    log_stage_spent::<S>(cfg, usage, self.judge_cost);

                    match res {
                        Ok(report) => {
                            self.attempts += 1;

                            Phase::Gate { report }
                        }
                        Err(blocker) => {
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
                    // need to clone these to circumvent borrow errors
                    let output = output.clone();
                    let report = report.clone();

                    if self.worker_cost + self.judge_cost >= cfg.stage_max_budget {
                        return Err(StageExecutionError::BudgetExhausted(None));
                    }
                    let (defects, cost_usd) = self.run_judge(&output, &report, cfg)?;
                    self.judge_cost = cost_usd;

                    if defects.is_empty() {
                        stage_info!(S::NAME, "judge found no defects, continuing");
                        log_stage_spent::<S>(cfg, self.worker_cost, self.judge_cost);
                        Phase::Done(output)
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
                Phase::Done(output) => return Ok(output.clone()),
            };
            self.phase = next;
            if let Some(on_change) = &mut on_change {
                on_change(&self);
            }
        }
    }

    fn get_agent(&mut self, cfg: &Config) -> Result<&mut Agent, AgentError> {
        if self.worker.is_none() {
            let mut agent = Agent::default();
            agent.set_briefing(format!("{}\n\n{WAIT_FOR_SUBAGENTS}", self.stage.briefing()));
            for (name, content) in self.stage.skills() {
                agent.add_skill(name, content);
            }
            agent.set_effort(cfg.effort);
            agent.set_model(cfg.model.clone());
            agent.set_dir(cfg.workspace_root()?);
            agent.set_json_schema(
                json_schema::<AgentResult<S::Report, S::Blocker>>().map_err(AgentError::from)?,
            );
            self.worker = Some(agent);
        }

        Ok(self.worker.as_mut().unwrap())
    }

    fn get_judge(&mut self, cfg: &Config, dir: &Path) -> Result<&mut Agent, AgentError> {
        if self.judge.is_none() {
            let mut judge = Agent::default();

            judge.set_briefing(WAIT_FOR_SUBAGENTS.to_owned());
            judge.set_effort(cfg.effort);
            judge.set_model(cfg.model.clone());
            judge.set_dir(dir.to_owned());
            judge.set_json_schema(json_schema::<Verdict>()?);

            self.judge = Some(judge);
        }

        Ok(self.judge.as_mut().unwrap())
    }

    /// Returns the agent's report, or the blocker it stopped the stage with.
    fn run_attempt(
        &mut self,
        cfg: &Config,
        prompt: String,
    ) -> Result<(Result<S::Report, S::Blocker>, f64), AgentError> {
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

        let agent = self.get_agent(cfg)?;
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

        // the worker agent is kept alive over the whole run
        // and claude code accumulated the usage over all
        // calls. So here we
        // override instead of adding.
        let usage_cost = agent.get_usage().cost_usd;

        Ok((result.result, usage_cost))
    }

    fn run_judge(
        &mut self,
        output: &S::Output,
        report: &S::Report,
        cfg: &Config,
    ) -> Result<(Vec<Defect>, f64), AgentError> {
        let remaining = cfg.stage_max_budget - self.worker_cost - self.judge_cost;

        let Some(base_prompt) = self.stage.judge_prompt(output, report) else {
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

        let judge = self.get_judge(cfg, &cfg.workspace_root()?)?;

        // Check if we are running a second judge attempt.
        // In that case, shift focus to the defects flagged in previous round.
        // If we don't do that, chances are the two agents never converge to a
        // passing version.
        let prompt = match judge.session_id() {
            Some(_) => format!(
                "{RECHECK_PROMPT}\n\nThe agent's report:\n{}",
                serde_json::to_string_pretty(report)?
            ),
            None => base_prompt,
        };

        judge.set_max_budget(remaining);

        let t = Instant::now();
        let judge_output = judge.run_with_hook(prompt, |s| stage_info!(S::NAME, "judge: {}", s))?;
        stage_info!(S::NAME, "judge finished in {}", format_elapsed(t));
        let verdict: Verdict = serde_json::from_str(&judge_output)?;
        Ok((verdict.defects, judge.get_usage().cost_usd))
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
