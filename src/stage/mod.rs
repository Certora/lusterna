pub mod translate;

use std::{error::Error, fmt};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    Config,
    agent::{Agent, AgentError},
};
pub trait Stage: Sized {
    type Input;

    type Output: Serialize;

    type SetupError: Error;

    type GateError: Error;

    // The stage's name.
    fn name() -> &'static str;

    /// Runs the stages setup phase.
    /// This phase is run before the agent is instructed to execute the stage.
    ///
    /// Runs (mechanical) check on the inputs before the stage itself is
    /// executed. "Mechanical" here means that they are deterministic checks
    /// and do **not** use an agent/LLM. If the check fails, the stage
    /// cannot be executed.
    fn setup(&mut self) -> Result<(), Self::SetupError>;

    /// Returns the base prompt of the agent
    fn base_prompt(&self) -> String;

    /// The stages briefing for the agent
    fn briefing(&self) -> String;

    /// Returns the feedback prompt for the agent for a failed gate check
    fn feedback_prompt(gate_error: &Self::GateError) -> String;

    /// The stage's gate checks. These check evaluate whether the agent that
    /// executes the stage worked correctly. If a gate check failed, then
    /// this is returned to the
    /// `agent_summary` is the agent's final message.
    fn gate_check(&self, agent_summary: &String) -> Result<Self::Output, Self::GateError>;

    /// The prompt for the judge, or `None` if the stage has no judge.
    /// By default, no judge is run.
    fn judge_prompt(&self, _output: &Self::Output, _agent_summary: &String) -> Option<String> {
        None
    }
}

#[derive(thiserror::Error)]
pub enum StageExecutionError<S: Stage> {
    SetupError(S::SetupError),
    MaxRetries(Option<AttemptRejected<S>>),
    AgentError(#[from] AgentError),
}

pub enum AttemptRejected<S: Stage> {
    Gate(S::GateError),
    Judge(Vec<Defect>),
}

pub fn run<S: Stage>(mut s: S, cfg: &Config) -> Result<S::Output, StageExecutionError<S>> {
    // Run the setup, if it fails, stop here.
    s.setup().map_err(StageExecutionError::SetupError)?;

    let mut prompt: String = s.base_prompt();
    let mut last_rejection: Option<AttemptRejected<S>> = None;

    let mut agent = Agent::with_name(S::name().to_ascii_uppercase());
    for _ in 0..cfg.max_stage_attempts {
        let summary = agent.run(prompt)?;
        match s.gate_check(&summary) {
            Ok(o) => match s.judge_prompt(&o, &summary) {
                Some(judge_prompt) => {
                    let defects = run_judge::<S>(judge_prompt)?;
                    if defects.is_empty() {
                        return Ok(o);
                    }
                    prompt = defects_feedback_prompt(&defects);
                    last_rejection = Some(AttemptRejected::Judge(defects))
                }
                None => return Ok(o),
            },
            Err(e) => {
                prompt = S::feedback_prompt(&e);
                last_rejection = Some(AttemptRejected::Gate(e));
            }
        }
    }
    return Err(StageExecutionError::MaxRetries(last_rejection));
}

/// A defect the judge found in a stage's output.
#[derive(Debug, Deserialize, JsonSchema)]
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

/// The judge's structured output. `--json-schema` requires an object at the
/// top level, so the defect list is wrapped in this struct. An empty `defects`
/// list approves the stage.
#[derive(Deserialize, JsonSchema)]
struct Verdict {
    defects: Vec<Defect>,
}

fn run_judge<S: Stage>(judge_prompt: String) -> Result<Vec<Defect>, AgentError> {
    let mut judge_agent = Agent::with_name(format!("{}_judge", S::name().to_ascii_uppercase()));
    let schema = schemars::schema_for!(Verdict);
    judge_agent.set_json_schema(serde_json::to_string(&schema)?);

    let judge_output = judge_agent.run(judge_prompt)?;
    let verdict: Verdict = serde_json::from_str(&judge_output)?;
    Ok(verdict.defects)
}
