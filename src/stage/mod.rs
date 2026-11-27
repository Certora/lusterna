pub(crate) mod exec;
pub mod formalize;
pub mod infer;
pub mod pre_translate;
pub mod properties;
pub mod prove;
pub mod report;
pub mod translate;

use std::{
    error::Error,
    fmt::{self, Display},
};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize, de::DeserializeOwned};

use crate::{
    agent::AgentOptions,
    config::{Config, Platform},
};

/// The `SKILL.md` of the skill `invariants`, which the formalize and prove
/// stages share.
const INVARIANTS_SKILL: &str = include_str!("res/invariants.md");

/// The `SKILL.md` of the skill `induction`, which the formalize and prove
/// stages share, with `{helper}` standing for the rule that defines a helper.
const INDUCTION_SKILL: &str = include_str!("res/induction.md");

/// Returns the `SKILL.md` of the skill `induction` for a crate of `platform`.
fn induction_skill(platform: &Platform) -> String {
    INDUCTION_SKILL.replace(
        "{helper}",
        match platform {
            Platform::Generic => {
                "A helper is a function that changes the struct, that the crate does not \
                 export, and whose every call in the Rust source of the crate is inside a \
                 function of the translation that changes the struct. The invariant only has \
                 to hold between operations, not in the middle of one. A function is exported \
                 if every module on its path from the crate root is `pub`, or if a `pub use` \
                 re-exports it. An exported function is never a helper. That a function is \
                 `pub` in its module does not decide whether the crate exports it."
            }
            Platform::Solana => {
                "A helper is a function that changes the struct and whose every call in the \
                 Rust source of the crate is inside a function of the translation that changes \
                 the struct. That a function is `pub`, or that the crate exports it, does not \
                 keep it from being a helper: code outside a Solana program can call only its \
                 instructions. A function with a call inside a function that the translation \
                 does not contain is not a helper."
            }
        },
    )
}

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
    /// What the agent did, which it returns when it finishes the stage.
    type Submission: Serialize + DeserializeOwned + JsonSchema + Clone;
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

    /// The tools and the environment of the agent. By default, the agent has
    /// the default `AgentOptions`.
    fn agent_options(&self, _cfg: &Config) -> AgentOptions {
        AgentOptions::default()
    }

    /// Returns the feedback prompt for the agent for a failed gate check
    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String;

    /// The stage's gate checks. These check evaluate whether the agent that
    /// executes the stage worked correctly. If a gate check failed, then
    /// this is returned to the
    /// `submission` is the agent's submission.
    fn gate_check(
        &self,
        submission: &Self::Submission,
    ) -> Result<Option<Self::GateFailure>, Self::GateExecutionError>;

    /// The prompt for the judge, or `None` if the stage has no judge.
    fn judge_prompt(&self, submission: &Self::Submission) -> Option<String>;
}

/// The blocker type of a stage that the agent cannot stop.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub enum Never {}

impl Display for Never {
    fn fmt(&self, _f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {}
    }
}

/// The options of an agent that builds Lean, which the formalize and prove
/// stages share: its commands run in the foreground, one at a time, Bash stops
/// a command after `cfg.command_timeout` minutes, Lean runs only through
/// `lake build`, whose lakefile limits its memory, and the agent runs in the
/// sandbox of `AgentOptions::sandbox`.
fn lean_agent_options(cfg: &Config) -> AgentOptions {
    let timeout_ms = (cfg.command_timeout * 60_000).to_string();
    let mut options = AgentOptions::default();
    options
        .disallowed_tools
        .extend(["Bash(lean *)".to_owned(), "Bash(lake env *)".to_owned()]);
    options.env = vec![
        (
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS".to_owned(),
            "1".to_owned(),
        ),
        ("BASH_DEFAULT_TIMEOUT_MS".to_owned(), timeout_ms.clone()),
        ("BASH_MAX_TIMEOUT_MS".to_owned(), timeout_ms),
    ];
    options.sandbox = true;
    options
}

/// Asserts that `rendered` contains no placeholder other than those in
/// `allowed`. A placeholder is a word of lowercase letters and underscores in
/// braces, such as `{crate_root}`.
#[cfg(test)]
fn assert_no_placeholders(rendered: &str, allowed: &[&str]) {
    let mut rest = rendered;
    while let Some(start) = rest.find('{') {
        rest = &rest[start..];
        let Some(end) = rest.find('}') else {
            break;
        };
        let word = &rest[1..end];
        let placeholder = &rest[..=end];
        let is_placeholder =
            !word.is_empty() && word.chars().all(|c| c.is_ascii_lowercase() || c == '_');
        assert!(
            !is_placeholder || allowed.contains(&placeholder),
            "{placeholder} is left"
        );
        rest = &rest[1..];
    }
}
