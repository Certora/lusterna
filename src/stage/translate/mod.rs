mod gate;
mod setup;

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::stage::{
    GateStop, Stage,
    translate::{gate::TranslateGateError, setup::SetupOutcome},
};

pub use {gate::TranslateGateFailure, setup::TranslateSetupErr};

/// Logs at info level with the translate stage's name as the log target.
macro_rules! translate_info {
    ($($arg:tt)+) => {
        $crate::log::stage_info!(
            <$crate::stage::translate::TranslateStage as $crate::stage::Stage>::name(),
            $($arg)+
        )
    };
}
use translate_info;

/// The agent briefing of the translate stage, with `{crate_root}`,
/// `{entrypoints}` and `{opaque}` standing for the crate root and the
/// Markdown lists of the Charon `--start-from` and `--opaque` patterns.
const BRIEFING_TEMPLATE: &str = include_str!("res/briefing.md");

/// The first prompt of the translate stage, with `{crate_root}` standing for
/// the crate root.
const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

/// The judge prompt of the translate stage, with `{crate_root}`,
/// `{setup_commit}`, `{briefing}` and `{agent_summary}` standing for the
/// crate root, the hash of the setup commit, the agent briefing and the
/// agent's final message.
const JUDGE_PROMPT_TEMPLATE: &str = include_str!("res/judge.md");

pub struct TranslateInput {
    /// Absolute path to the workspace of the Rust project.
    pub workspace_root: PathBuf,
    /// If the workspace has more than one crate, this must the absolute path to
    /// the crate. In that case, `workspace_root` must be a parent of this
    /// path. If this is empty, then the stage assumes the `workspace_root`
    /// is also the `crate` root
    pub crate_root: Option<PathBuf>,

    /// The entrypoint pattern passed to Aeneas/Charon to be translated
    pub entrypoints: Vec<String>,

    /// The opaque pattern passed to Aeneas/Charon to left opaque
    pub opaque: Vec<String>,

    /// The Aeneas git revision the Lean project depends on
    pub aeneas_rev: String,

    /// The Lean toolchain of that Aeneas revision, as in its
    /// `backends/lean/lean-toolchain`
    pub lean_toolchain: String,
}

impl TranslateInput {
    /// Returns `crate_root` if given, and `workspace_root` otherwise.
    pub fn crate_root(&self) -> &Path {
        self.crate_root.as_deref().unwrap_or(&self.workspace_root)
    }
}

#[derive(Serialize)]
pub struct TranslateOutput {
    // Absolute path of the crate's root
    pub crate_root: PathBuf,
    /// Absolute path to the extraction script
    pub extract_script: PathBuf,
    /// Absolute path to the directory of the Lean modules that Aeneas
    /// generated
    pub extracted: PathBuf,
    /// Absolute path to the lean project root dir
    pub lean_root: PathBuf,
    /// The summary of the agent that passed the checks.
    pub agent_summary: String,
}

pub struct TranslateStage {
    setup_outcome: Option<SetupOutcome>,

    input: TranslateInput,
}

impl TranslateStage {
    /// Constructs a new stage from the given input.
    ///
    /// Must not have side effects.
    pub fn new(input: TranslateInput) -> Self {
        Self {
            setup_outcome: None,
            input,
        }
    }
}

/// Returns the first prompt for the crate at `crate_root`.
fn make_base_prompt(crate_root: &Path) -> String {
    BASE_PROMPT_TEMPLATE.replace("{crate_root}", &crate_root.display().to_string())
}

/// Returns the agent briefing for the crate at `crate_root` and the given
/// Charon `--start-from` and `--opaque` patterns.
///
/// `crate_root` must not contain `{entrypoints}` or `{opaque}`, and the
/// entrypoints must not contain `{opaque}`, because each value is inserted
/// before the next placeholder is replaced.
fn make_briefing(crate_root: &Path, entrypoints: &[String], opaque: &[String]) -> String {
    let list = |patterns: &[String]| {
        if patterns.is_empty() {
            return "(none)".to_owned();
        }
        patterns
            .iter()
            .map(|p| format!("- `{p}`"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    BRIEFING_TEMPLATE
        .replace("{crate_root}", &crate_root.display().to_string())
        .replace("{entrypoints}", &list(entrypoints))
        .replace("{opaque}", &list(opaque))
}

impl Stage for TranslateStage {
    type Input = TranslateInput;

    type Output = TranslateOutput;

    type SetupError = TranslateSetupErr;

    type GateFailure = TranslateGateFailure;

    type GateExecutionError = TranslateGateError;

    fn name() -> &'static str {
        "translate"
    }

    fn setup(&mut self) -> Result<(), Self::SetupError> {
        let outcome = setup::run_setup(&self.input)?;
        self.setup_outcome = Some(outcome);
        Ok(())
    }

    fn base_prompt(&self) -> String {
        make_base_prompt(self.input.crate_root())
    }

    fn feedback_prompt(gate_error: &Self::GateFailure) -> String {
        gate_error.to_feedback_prompt()
    }

    fn gate_check(&self, agent_summary: &str) -> Result<Self::Output, GateStop<Self>> {
        let setup_outcome = self.setup_outcome.as_ref().expect("Setup not run");

        gate::run_gate(&self.input, setup_outcome)?;
        Ok(TranslateOutput {
            crate_root: self.input.crate_root().to_owned(),
            extract_script: setup_outcome.extraction_script.to_owned(),
            extracted: setup_outcome.lean_root.join("Extraction"),
            lean_root: setup_outcome.lean_root.to_owned(),
            agent_summary: agent_summary.to_string(),
        })
    }

    fn briefing(&self) -> String {
        let crate_root = self.input.crate_root();
        make_briefing(crate_root, &self.input.entrypoints, &self.input.opaque)
    }

    fn judge_prompt(&self, output: &Self::Output, agent_summary: &String) -> Option<String> {
        let setup_outcome = self.setup_outcome.as_ref().expect("Setup not run");
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace("{crate_root}", &output.crate_root.display().to_string())
                .replace("{setup_commit}", &setup_outcome.setup_commit)
                .replace("{briefing}", &self.briefing())
                .replace("{agent_summary}", agent_summary),
        )
    }
}
