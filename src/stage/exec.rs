use std::{fmt, path::Path};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{
    agent::{Agent, AgentError},
    config::Config,
    log::stage_info,
    stage::{GateStop, Stage},
};

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq, Eq)]
pub enum AttemptRejected<S: Stage> {
    Gate(S::GateFailure),
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

/// The judge's structured output. `--json-schema` requires an object at the
/// top level, so the defect list is wrapped in this struct. An empty `defects`
/// list approves the stage.
#[derive(Deserialize, JsonSchema)]
struct Verdict {
    defects: Vec<Defect>,
}

#[derive(thiserror::Error)]
pub enum StageExecutionError<S: Stage> {
    #[error("setup failed: {0}")]
    SetupError(S::SetupError),
    #[error("failed to execute the gate: {0}")]
    GateExecutionError(S::GateExecutionError),
    #[error("max attempts reached{}", .0.as_ref().map(|r| format!(", {r}")).unwrap_or_default())]
    MaxRetries(Option<AttemptRejected<S>>),
    #[error("budget exhausted{}", .0.as_ref().map(|r| format!(", {r}")).unwrap_or_default())]
    BudgetExhausted(Option<AttemptRejected<S>>),
    #[error("the agent stopped the stage: {0}")]
    Blocked(S::Blocker),
    #[error(transparent)]
    AgentError(#[from] AgentError),
}

impl<S: Stage> fmt::Debug for StageExecutionError<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(self, f)
    }
}

// The agent's structured output: its report, or the reason why it stopped
// the stage. `--json-schema` requires an object at the top level, so the
// `Result` is in a field.
#[derive(Deserialize, JsonSchema)]
struct AgentResult<R, B> {
    result: Result<R, B>,
}

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

    pub fn run(mut self, cfg: &Config, workdir: &Path) -> Result<(), StageExecutionError<S>> {
        let mut agent = self.init_agent(cfg, workdir)?;
        loop {
            let next = match &self.phase {
                Phase::Attempt { prompt } => match self.run_attempt(&mut agent, prompt.clone())? {
                    Ok(report) => {
                        self.attempts += 1;
                        // the worker agent is kept alive over the whole run
                        // and claude code accumulated the usage over all calls.
                        // So here we override instead of adding.
                        self.worker_cost = agent.get_usage().cost_usd;
                        Phase::Gate { report }
                    }
                    Err(blocker) => return Err(StageExecutionError::Blocked(blocker)),
                },
                Phase::Gate { report } => {
                    match self.stage.gate_check(report) {
                        Ok(o) => Phase::Judge {
                            report: report.clone(),
                            output: o.clone(),
                        },
                        Err(GateStop::Failure(failure)) => {
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
                        let (defects, cost_usd) = self.run_judge(prompt, cfg, workdir)?;
                        self.judge_cost += cost_usd;
                        if defects.is_empty() {
                            Phase::Done(output.clone())
                        } else {
                            self.retry(cfg, AttemptRejected::Judge(defects))?
                        }
                    } else {
                        Phase::Done(output.clone())
                    }
                }
                Phase::Done(_) => todo!(),
            };
            self.phase = next
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
        agent: &mut Agent,
        prompt: String,
    ) -> Result<Result<S::Report, S::Blocker>, AgentError> {
        let output =
            agent.run_with_hook(prompt, |s| stage_info!(S::NAME.as_str(), "agent: {}", s))?;

        let result: AgentResult<S::Report, S::Blocker> = serde_json::from_str(&output)?;
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

        let judge_output =
            judge_agent.run_with_hook(prompt, |s| stage_info!(S::NAME.as_str(), "judge: {}", s))?;
        let verdict: Verdict = serde_json::from_str(&judge_output)?;
        Ok((verdict.defects, judge_agent.get_usage().cost_usd))
    }

    fn retry(
        &self,
        cfg: &Config,
        rejection: AttemptRejected<S>,
    ) -> Result<Phase<S>, StageExecutionError<S>> {
        if self.attempts > cfg.stage_max_attempts {
            return Err(StageExecutionError::MaxRetries(Some(rejection)));
        }
        if self.judge_cost + self.worker_cost >= cfg.stage_max_budget {
            return Err(StageExecutionError::BudgetExhausted(Some(rejection)));
        }
        let mut prompt = match rejection {
            AttemptRejected::Gate(_) => todo!(),
            AttemptRejected::Judge(defects) => defects_feedback_prompt(&defects),
        };
        prompt.push_str(
            "\nIn your report, list every change that you made since the start of the stage.\n",
        );
        Ok(Phase::Attempt { prompt })
    }
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

/// Returns the JSON schema of `T` for `claude --json-schema`, which only
/// accepts draft-07 schemas. `schema_for!` generates a draft 2020-12 schema,
/// which it rejects.
fn json_schema<T: JsonSchema>() -> Result<String, serde_json::Error> {
    let schema = schemars::generate::SchemaSettings::draft07()
        .into_generator()
        .into_root_schema_for::<T>();
    serde_json::to_string(&schema)
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
