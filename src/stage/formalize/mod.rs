mod gate;

use std::{collections::BTreeMap, fs, io, path::PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    agent::AgentOptions,
    config::Config,
    stage::{
        INDUCTION_SKILL, INVARIANTS_SKILL, Stage, StageName, lean_agent_options,
        properties::{Properties, PropertyId},
    },
    utils::git::{self, GitError},
};

pub use gate::{FormalizeGateError, FormalizeGateFailure};

/// The first prompt of the formalize stage, with `{lean_root}` standing for
/// the Lean project root.
const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

/// The agent briefing of the formalize stage, with `{lean_root}`,
/// `{extracted}`, `{properties}`, `{crate_root}` and `{entrypoints}` standing
/// for the Lean project root, the directory of the extraction, the JSON file
/// of the properties, the root of the crate and its entrypoints.
const BRIEFING_TEMPLATE: &str = include_str!("res/briefing.md");

/// The `SKILL.md` of the skill `aeneas-specs`.
const AENEAS_SPECS_SKILL: &str = include_str!("res/aeneas-specs.md");

/// The `SKILL.md` of the skill `aeneas-models`.
const AENEAS_MODELS_SKILL: &str = include_str!("res/aeneas-models.md");

/// The judge prompt of the formalize stage, with `{lean_root}`,
/// `{extracted}`, `{properties}`, `{crate_root}`, `{briefing}`, `{skills}`
/// and `{submission}` standing for the Lean project root, the directory of the
/// extraction, the JSON file of the properties, the root of the crate, the
/// agent briefing, the `SKILL.md` content of the agent's skills and the
/// agent's submission.
const JUDGE_PROMPT_TEMPLATE: &str = include_str!("res/judge.md");

#[derive(Deserialize, Serialize)]
pub struct FormalizeStage {
    input: FormalizeInput,
    /// The content of `input.properties`.
    properties: Properties,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct FormalizeInput {
    /// Absolute path to the directory of the Lean modules that Aeneas
    /// generated
    pub extracted: PathBuf,
    /// Path to the lean root, from which lake can be executed.
    pub lean_root: PathBuf,
    /// Absolute path to the JSON file of the properties to formalize
    pub properties: PathBuf,
    /// Absolute path to the root of the crate
    pub crate_root: PathBuf,
    /// The Rust paths from which the translation covers the reachable code
    pub entrypoints: Vec<String>,
}

/// A problem outside `Spec/` that stops the stage, for example a dependency of
/// the Lean project that does not build.
#[derive(Debug, derive_more::Display, JsonSchema, Serialize, Deserialize)]
#[display("{reason}")]
pub struct FormalizeBlocker {
    /// What the problem is, and the command and output that show it.
    pub reason: String,
}

#[derive(Debug, thiserror::Error)]
pub enum FormalizeSetupError {
    #[error("the work tree of `{0}` has uncommitted changes")]
    Dirty(PathBuf),
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error(transparent)]
    Git(#[from] GitError),
    #[error("cannot parse the properties in `{0}`: {1}")]
    Properties(PathBuf, serde_json::Error),
}

#[derive(Debug, Clone, JsonSchema, Serialize, Deserialize)]
pub enum SkipReason {
    /// The given items are necessary for proving the property, but was not
    /// translated, i.e., is not present in the Lean extraction
    MissingTranslation { items: Vec<String> },
    /// The property itself is ill-stated and cannot be formalized.
    /// This does **not** mean it is violated or unprovable, but
    /// that it simply is not expressible.
    IllStated { reason: String },
    /// Something else
    Other(String),
}

#[derive(Debug, Clone, JsonSchema, Serialize, Deserialize)]
pub enum PropertyFormalizationStatus {
    Formalized {
        /// The spec file, relative to the Lean project root.
        file: PathBuf,
        /// The full Lean names of the theorems that express the property.
        theorems: Vec<String>,
        /// The induction steps whose operation the natspec names as an
        /// exception to the invariant, by full Lean name, each with the reason
        /// that the natspec gives.
        exceptions: BTreeMap<String, String>,
    },
    Skipped(SkipReason),
}

/// The functions that the induction over the invariants of a struct covers.
#[derive(Debug, Clone, JsonSchema, Serialize, Deserialize)]
pub struct Induction {
    /// The full Lean names of the functions that create the struct, which the
    /// base cases call.
    pub base_cases: Vec<String>,
    /// The full Lean names of the mutating operations of the struct, which
    /// the induction steps call.
    pub steps: Vec<String>,
    /// The full Lean names of the unmodelled operations of the struct, each
    /// with the value of the struct that it changes and the write that the
    /// translation drops.
    pub unmodelled_steps: BTreeMap<String, String>,
    /// The full Lean names of the helpers of the struct, each with the full
    /// Lean names of the functions of the translation that call it. A helper
    /// changes the struct but is never the outermost function that changes
    /// it: every call of it is inside another function that changes the
    /// struct, so the operation that calls it covers its change.
    pub helpers: BTreeMap<String, Vec<String>>,
}

#[derive(Debug, Clone, JsonSchema, Serialize, Deserialize)]
pub struct FormalizeSubmission {
    pub property_status: BTreeMap<PropertyId, PropertyFormalizationStatus>,
    /// For each struct that an invariant property is declared on, keyed by the
    /// struct name as the property gives it, the functions of its base cases
    /// and induction steps.
    pub induction: BTreeMap<String, Induction>,
    /// The full Lean names of the theorems that this run added. A renamed
    /// theorem is not one of them.
    pub new_theorems: Vec<String>,
    /// The full Lean names of the theorems from before this run whose
    /// statement this run changed.
    pub updated_theorems: Vec<String>,
    /// The full Lean names that the theorems that this run removed or renamed
    /// had before this run.
    pub removed_theorems: Vec<String>,
}

impl Stage for FormalizeStage {
    const NAME: super::StageName = StageName::Formalize;

    type Input = FormalizeInput;

    type Submission = FormalizeSubmission;

    type Blocker = FormalizeBlocker;

    type GateFailure = FormalizeGateFailure;

    type GateExecutionError = FormalizeGateError;

    type SetupError = FormalizeSetupError;

    fn setup(input: Self::Input) -> Result<Self, Self::SetupError> {
        if !git::is_clean(&input.lean_root)? {
            return Err(FormalizeSetupError::Dirty(input.lean_root));
        }
        let properties = serde_json::from_str(&fs::read_to_string(&input.properties)?)
            .map_err(|e| FormalizeSetupError::Properties(input.properties.clone(), e))?;
        fs::create_dir_all(input.lean_root.join("Spec"))?;
        Ok(Self { input, properties })
    }

    fn base_prompt(&self) -> String {
        BASE_PROMPT_TEMPLATE.replace("{lean_root}", &self.input.lean_root.display().to_string())
    }

    fn briefing(&self) -> String {
        BRIEFING_TEMPLATE
            .replace("{lean_root}", &self.input.lean_root.display().to_string())
            .replace("{extracted}", &self.input.extracted.display().to_string())
            .replace("{properties}", &self.input.properties.display().to_string())
            .replace("{crate_root}", &self.input.crate_root.display().to_string())
            .replace(
                "{entrypoints}",
                &self
                    .input
                    .entrypoints
                    .iter()
                    .map(|e| format!("`{e}`"))
                    .collect::<Vec<_>>()
                    .join(", "),
            )
    }

    fn agent_options(&self, cfg: &Config) -> AgentOptions {
        lean_agent_options(cfg)
    }

    fn skills(&self) -> Vec<(&'static str, String)> {
        vec![
            ("aeneas-specs", AENEAS_SPECS_SKILL.to_owned()),
            ("aeneas-models", AENEAS_MODELS_SKILL.to_owned()),
            ("induction", INDUCTION_SKILL.to_owned()),
            ("invariants", INVARIANTS_SKILL.to_owned()),
        ]
    }

    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String {
        gate_failed.to_feedback_prompt()
    }

    fn gate_check(
        &self,
        submission: &Self::Submission,
    ) -> Result<Option<FormalizeGateFailure>, FormalizeGateError> {
        gate::run_gate(&self.input.lean_root, &self.properties, submission)
    }

    fn judge_prompt(&self, submission: &Self::Submission) -> Option<String> {
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace("{lean_root}", &self.input.lean_root.display().to_string())
                .replace("{extracted}", &self.input.extracted.display().to_string())
                .replace("{properties}", &self.input.properties.display().to_string())
                .replace("{crate_root}", &self.input.crate_root.display().to_string())
                .replace(
                    "{skills}",
                    &self
                        .skills()
                        .into_iter()
                        .map(|(_, content)| content)
                        .collect::<Vec<_>>()
                        .join("\n\n"),
                )
                .replace("{briefing}", &self.briefing())
                .replace(
                    "{submission}",
                    &serde_json::to_string_pretty(submission).expect("the submission serializes"),
                ),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stage::assert_no_placeholders;

    fn stage() -> FormalizeStage {
        FormalizeStage {
            input: FormalizeInput {
                extracted: PathBuf::from("/c/lusterna/lean/Extraction"),
                lean_root: PathBuf::from("/c/lusterna/lean"),
                properties: PathBuf::from("/c/lusterna/properties.json"),
                crate_root: PathBuf::from("/c"),
                entrypoints: vec!["crate::vault".to_owned()],
            },
            properties: Properties(Vec::new()),
        }
    }

    /// A submission that skips property 1 with the reason `reason`
    fn submission(reason: &str) -> FormalizeSubmission {
        FormalizeSubmission {
            property_status: BTreeMap::from([(
                1,
                PropertyFormalizationStatus::Skipped(SkipReason::Other(reason.to_owned())),
            )]),
            induction: BTreeMap::new(),
            new_theorems: Vec::new(),
            updated_theorems: Vec::new(),
            removed_theorems: Vec::new(),
        }
    }

    #[test]
    fn briefing_fills_every_placeholder() {
        let briefing = stage().briefing();

        assert_no_placeholders(&briefing, &[]);
        assert!(briefing.contains("`/c/lusterna/properties.json`"));
    }

    #[test]
    fn judge_prompt_fills_every_placeholder() {
        let prompt = stage().judge_prompt(&submission("no model")).unwrap();

        assert_no_placeholders(&prompt, &[]);
        assert!(prompt.contains("name: aeneas-specs"));
    }

    #[test]
    fn judge_prompt_keeps_placeholders_in_the_submission() {
        let prompt = stage().judge_prompt(&submission("{lean_root}")).unwrap();

        assert!(prompt.contains("\"Other\": \"{lean_root}\""));
    }

    #[test]
    fn base_prompt_names_lean_root() {
        assert!(stage().base_prompt().contains("`/c/lusterna/lean`"));
    }

    #[test]
    fn skill_has_front_matter() {
        let skills = stage().skills();

        assert_eq!(skills.len(), 4);
        for (name, content) in skills {
            assert!(content.starts_with(&format!("---\nname: {name}\n")));
        }
    }
}
