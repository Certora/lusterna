pub mod translate;

use std::error::Error;

use serde::Serialize;

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
    fn gate_check(&self, agent_summary: String) -> Result<Self::Output, Self::GateError>;
}

#[derive(thiserror::Error)]
pub enum StageExecutionError<S: Stage> {
    SetupError(S::SetupError),
    MaxRetries(Option<S::GateError>),
    AgentError(#[from] AgentError),
}

pub fn run<S: Stage>(mut s: S, cfg: &Config) -> Result<S::Output, StageExecutionError<S>> {
    // Run the setup, if it fails, stop here.
    s.setup().map_err(StageExecutionError::SetupError)?;

    let mut prompt: String = s.base_prompt();
    let mut last_err = None;

    let mut agent = Agent::with_name(S::name().to_ascii_uppercase());
    for _ in 0..cfg.max_stage_attempts {
        let summary = agent.run(&prompt)?;
        match s.gate_check(summary) {
            Ok(o) => return Ok(o),
            Err(e) => {
                prompt = S::feedback_prompt(&e);
                last_err = Some(e);
            }
        }
    }
    return Err(StageExecutionError::MaxRetries(last_err));
}
