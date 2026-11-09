mod gate;
mod report;
mod setup;
mod stubs;

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::{
    config::Platform,
    stage::{
        GateStop, Stage, StageName,
        translate::{gate::TranslateGateError, setup::TranslateSetupOutcome},
    },
};

pub use {
    gate::TranslateGateFailure,
    report::{TranslateBlocker, TranslateReport},
    setup::TranslateSetupErr,
    stubs::StubsError,
};

/// Logs at info level with the translate stage's name as the log target.
macro_rules! translate_info {
    ($($arg:tt)+) => {
        $crate::log::stage_info!(
            <$crate::stage::translate::TranslateStage as $crate::stage::Stage>::NAME,
            $($arg)+
        )
    };
}
use translate_info;

/// The agent briefing of the translate stage, with `{crate_root}`,
/// `{entrypoints}` and `{opaque}` standing for the crate root and the
/// Markdown lists of the Charon `--start-from` and `--opaque` patterns, and
/// `{solana_skill}` standing for the sentence that names the skill
/// `solana-translate`, or nothing.
const BRIEFING_TEMPLATE: &str = include_str!("res/briefing.md");

/// The first prompt of the translate stage, with `{crate_root}` standing for
/// the crate root.
const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

/// The `SKILL.md` of the skill `aeneas-recipes`, with `{aeneas_root}`
/// standing for the Aeneas checkout.
const AENEAS_RECIPES_SKILL: &str = include_str!("res/aeneas-recipes.md");

/// The `SKILL.md` of the skill `solana-translate`, with
/// `{lusterna_lean_root}` standing for `LUSTERNA_LEAN` and `{replaced_crates}`
/// for the list of the crates that `lusterna-stubs` replaces.
const SOLANA_TRANSLATE_SKILL: &str = include_str!("res/solana-translate.md");

/// The Lean package `LusternaLib`.
const LUSTERNA_LEAN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/lusterna-lean");

/// The Lean package `LusternaSpec`.
const LUSTERNA_SPEC: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/lusterna-spec");

/// The judge prompt of the translate stage, with `{crate_root}`,
/// `{setup_commit}`, `{briefing}`, `{skills}` and `{report}` standing for the
/// crate root, the hash of the setup commit, the agent briefing, the
/// `SKILL.md` content of the agent's skills and the agent's report.
const JUDGE_PROMPT_TEMPLATE: &str = include_str!("res/judge.md");

/// The default Charon `--opaque` patterns, one per line.
pub const DEFAULT_OPAQUE: &str = include_str!("res/default-opaque.txt");

/// The default Charon `--opaque` patterns for crates that depend on
/// `solana-program`, one per line.
const SOLANA_OPAQUE: &str = include_str!("res/solana-opaque.txt");

/// The default Charon `--opaque` patterns for crates that depend on
/// `solana-program`: `SOLANA_OPAQUE` and the items of the crates that
/// `lusterna-stubs` replaces.
pub fn solana_opaque() -> Vec<String> {
    SOLANA_OPAQUE
        .lines()
        .map(str::to_owned)
        .chain(stubs::opaque_patterns())
        .collect()
}

#[derive(Serialize, Deserialize, Clone)]
pub struct TranslateInput {
    /// Absolute path to the workspace of the Rust project.
    pub workspace_root: PathBuf,
    /// Absolute path to the crate: `workspace_root`, or a directory inside it.
    pub crate_root: PathBuf,

    /// The entrypoint pattern passed to Aeneas/Charon to be translated
    pub entrypoints: Vec<String>,

    /// The opaque pattern passed to Aeneas/Charon to left opaque
    pub opaque: Vec<String>,

    /// Absolute path to the Aeneas checkout
    pub aeneas_root: PathBuf,

    /// The Aeneas git revision the Lean project depends on
    pub aeneas_rev: String,

    /// The Lean toolchain of that Aeneas revision, as in its
    /// `backends/lean/lean-toolchain`
    pub lean_toolchain: String,

    /// The platform of the crate
    pub platform: Platform,
}

#[derive(Serialize, Deserialize, Clone)]
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
    /// The report of the agent that passed the checks.
    pub report: TranslateReport,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct TranslateStage {
    setup_outcome: TranslateSetupOutcome,
    input: TranslateInput,
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
fn make_briefing(
    crate_root: &Path,
    entrypoints: &[String],
    opaque: &[String],
    platform: &Platform,
) -> String {
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
        .replace(
            "{solana_skill}",
            match platform {
                Platform::Generic => "",
                Platform::Solana { .. } => " Also read the skill `lusterna:solana-translate`.",
            },
        )
}

impl Stage for TranslateStage {
    type Input = TranslateInput;

    type Output = TranslateOutput;

    type Report = TranslateReport;

    type Blocker = TranslateBlocker;

    type GateFailure = TranslateGateFailure;

    type GateExecutionError = TranslateGateError;

    type SetupError = TranslateSetupErr;

    const NAME: super::StageName = StageName::Translate;

    fn setup(input: Self::Input) -> Result<Self, Self::SetupError> {
        let setup_outcome = setup::run_setup(&input)?;
        Ok(Self {
            setup_outcome,
            input: input.clone(),
        })
    }

    fn base_prompt(&self) -> String {
        make_base_prompt(&self.input.crate_root)
    }

    fn feedback_prompt(gate_error: &Self::GateFailure) -> String {
        gate_error.to_feedback_prompt()
    }

    fn gate_check(&self, report: &TranslateReport) -> Result<Self::Output, GateStop<Self>> {
        let setup_outcome = &self.setup_outcome;

        gate::run_gate(&self.input, setup_outcome)?;
        Ok(TranslateOutput {
            crate_root: self.input.crate_root.clone(),
            extract_script: setup_outcome.extraction_script.to_owned(),
            extracted: setup_outcome.lean_root.join("Extraction"),
            lean_root: setup_outcome.lean_root.to_owned(),
            report: report.clone(),
        })
    }

    fn briefing(&self) -> String {
        let crate_root = &self.input.crate_root;
        make_briefing(
            crate_root,
            &self.input.entrypoints,
            &self.input.opaque,
            &self.input.platform,
        )
    }

    fn skills(&self) -> Vec<(&'static str, String)> {
        let mut skills = vec![(
            "aeneas-recipes",
            AENEAS_RECIPES_SKILL.replace(
                "{aeneas_root}",
                &self.input.aeneas_root.display().to_string(),
            ),
        )];
        if let Platform::Solana { .. } = &self.input.platform {
            let replaced = stubs::all()
                .iter()
                .map(|r| format!("`{}`", r.krate))
                .collect::<Vec<_>>()
                .join(", ");
            skills.push((
                "solana-translate",
                SOLANA_TRANSLATE_SKILL
                    .replace("{lusterna_lean_root}", LUSTERNA_LEAN)
                    .replace("{replaced_crates}", &replaced),
            ));
        }
        skills
    }

    fn judge_prompt(&self, output: &Self::Output, report: &TranslateReport) -> Option<String> {
        let setup_outcome = &self.setup_outcome;
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace("{crate_root}", &output.crate_root.display().to_string())
                .replace("{setup_commit}", &setup_outcome.setup_commit)
                .replace("{briefing}", &self.briefing())
                .replace(
                    "{skills}",
                    &self
                        .skills()
                        .into_iter()
                        .map(|(_, content)| content)
                        .collect::<Vec<_>>()
                        .join("\n\n"),
                )
                .replace(
                    "{report}",
                    &serde_json::to_string_pretty(report).expect("the report serializes"),
                ),
        )
    }
}
