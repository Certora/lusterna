pub(crate) mod exec;
pub mod formalize;
pub mod pre_translate;
pub mod prove;
pub mod translate;

use std::{
    error::Error,
    fmt::{self, Display},
};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize, de::DeserializeOwned};

#[derive(Debug, Serialize, Deserialize, derive_more::Display)]
pub enum StageName {
    Orientate,
    Infer,
    PreTranslate,
    Translate,
    Formalize,
    Prove,
    Refute,
    Report,
}

#[derive(Debug, thiserror::Error, derive_more::Display)]
pub struct InvalidTypeName(String);

impl TryFrom<&str> for StageName {
    type Error = InvalidTypeName;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value.to_ascii_lowercase().as_str() {
            "orientate" => Ok(StageName::Orientate),
            "infer" => Ok(StageName::Infer),
            "pre-translate" | "pretranslate" => Ok(StageName::PreTranslate),
            "translate" => Ok(StageName::Translate),
            "formalize" => Ok(StageName::Formalize),
            "prove" => Ok(StageName::Prove),
            "refute" => Ok(StageName::Refute),
            "report" => Ok(StageName::Report),
            _ => Err(InvalidTypeName(value.to_owned())),
        }
    }
}

impl StageName {
    pub fn as_str(&self) -> &'static str {
        match self {
            StageName::Orientate => "orientate",
            StageName::Infer => "infer",
            StageName::PreTranslate => "pre-translate",
            StageName::Translate => "translate",
            StageName::Formalize => "formalize",
            StageName::Prove => "prove",
            StageName::Refute => "refute",
            StageName::Report => "report",
        }
    }
}

pub trait Stage: Sized + Serialize + DeserializeOwned {
    const NAME: StageName;
    /// The stage's input.
    type Input: Serialize + DeserializeOwned;
    /// The stage's output; used to feed the subsequent stage.
    type Output: Serialize + DeserializeOwned + Clone;
    /// What the agent did, which it returns when it finishes the stage.
    type Report: Serialize + DeserializeOwned + JsonSchema + Clone;
    /// The reasons for which the agent can stop the stage. Use [`Never`] for
    /// a stage that the agent cannot stop.
    type Blocker: Display + Serialize + DeserializeOwned + JsonSchema;
    /// The reason why the gate did not pass.
    /// The function `[feedback_prompt]` converts this type to a feedback prompt
    /// that is passed to the agent, so the agent can fix the failure.
    ///
    /// The `[GateExecutionError]` on the other hand is used to indicate
    /// something failed while running the gate checks. This is not passed
    /// back to agent and instead abort the stage.
    type GateFailure: Display + Serialize + DeserializeOwned + Clone;

    /// An error that can occur while running the gate.
    /// This is meant for errors that the agent can not fix, use `[GateFailure]`
    /// for this instead.
    type GateExecutionError: Error;

    type SetupError: Error;

    /// Runs the stages setup phase.
    /// This phase is run before the agent is instructed to execute the stage.
    ///
    /// Runs (mechanical) check on the inputs before the stage itself is
    /// executed. "Mechanical" here means that they are deterministic checks
    /// and do **not** use an agent/LLM. If the check fails, the stage
    /// cannot be executed.
    fn setup(input: Self::Input) -> Result<Self, Self::SetupError>;

    /// Returns the base prompt of the agent
    fn base_prompt(&self) -> String;

    /// The stages briefing for the agent
    fn briefing(&self) -> String;

    /// The name and the `SKILL.md` content of each skill of the agent.
    /// By default, the agent has no skills.
    fn skills(&self) -> Vec<(&'static str, String)> {
        Vec::new()
    }

    /// Returns the feedback prompt for the agent for a failed gate check
    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String;

    /// The stage's gate checks. These check evaluate whether the agent that
    /// executes the stage worked correctly. If a gate check failed, then
    /// this is returned to the
    /// `report` is the agent's report.
    fn gate_check(&self, report: &Self::Report) -> Result<Self::Output, GateStop<Self>>;

    /// The prompt for the judge, or `None` if the stage has no judge.
    /// By default, no judge is run.
    fn judge_prompt(&self, _output: &Self::Output, _report: &Self::Report) -> Option<String> {
        None
    }
}

/// The blocker type of a stage that the agent cannot stop.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub enum Never {}

impl Display for Never {
    fn fmt(&self, _f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {}
    }
}

/// Why a check stopped the gate.
pub enum GateStop<S: Stage> {
    Failure(S::GateFailure),
    Error(S::GateExecutionError),
}

impl<S: Stage> fmt::Debug for GateStop<S>
where
    S::GateFailure: fmt::Debug,
{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GateStop::Failure(e) => f.debug_tuple("Failure").field(e).finish(),
            GateStop::Error(e) => f.debug_tuple("Error").field(e).finish(),
        }
    }
}
