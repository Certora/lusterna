mod gate;
pub mod properties;

use std::{collections::BTreeMap, fs, io, path::PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    stage::{
        Stage, StageName,
        formalize::properties::{Properties, PropertyId},
    },
    utils::git::{self, GitError},
};

pub use gate::{FormalizeGateError, FormalizeGateFailure};

/// The first prompt of the formalize stage, with `{lean_root}` standing for
/// the Lean project root.
const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

/// The agent briefing of the formalize stage, with `{lean_root}`,
/// `{extracted}` and `{properties}` standing for the Lean project root, the
/// directory of the extraction and the JSON file of the properties.
const BRIEFING_TEMPLATE: &str = include_str!("res/briefing.md");

/// The `SKILL.md` of the skill `aeneas-specs`.
const AENEAS_SPECS_SKILL: &str = include_str!("res/aeneas-specs.md");

/// The judge prompt of the formalize stage, with `{lean_root}`,
/// `{extracted}`, `{properties}`, `{briefing}`, `{skills}` and `{report}`
/// standing for the Lean project root, the directory of the extraction, the
/// JSON file of the properties, the agent briefing, the `SKILL.md` content of
/// the agent's skills and the agent's report.
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
}

#[derive(Debug, derive_more::Display, JsonSchema, Serialize, Deserialize)]
pub struct FormalizeBlocker;

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
pub enum PropertyStatus {
    Formalized {
        /// The spec file, relative to the Lean project root.
        file: PathBuf,
        /// The full Lean names of the theorems that express the property.
        theorems: Vec<String>,
    },
    Skipped(SkipReason),
}

#[derive(Debug, Clone, JsonSchema, Serialize, Deserialize)]
pub struct FormalizeOutput {
    pub property_status: BTreeMap<PropertyId, PropertyStatus>,
    /// For each struct that an invariant property is declared on, keyed by the
    /// struct name as the property gives it, the full Lean names of its
    /// mutating operations, which the induction steps of its invariants call.
    pub mutating_operations: BTreeMap<String, Vec<String>>,
}

impl Stage for FormalizeStage {
    const NAME: super::StageName = StageName::Formalize;

    type Input = FormalizeInput;

    type Output = FormalizeOutput;

    type Report = FormalizeOutput;

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
    }

    fn skills(&self) -> Vec<(&'static str, String)> {
        vec![("aeneas-specs", AENEAS_SPECS_SKILL.to_owned())]
    }

    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String {
        gate_failed.to_feedback_prompt()
    }

    fn gate_check(&self, report: &Self::Report) -> Result<Self::Output, super::GateStop<Self>> {
        gate::run_gate(&self.input.lean_root, &self.properties, report)?;
        Ok(report.clone())
    }

    fn judge_prompt(&self, _output: &Self::Output, report: &Self::Report) -> Option<String> {
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace("{lean_root}", &self.input.lean_root.display().to_string())
                .replace("{extracted}", &self.input.extracted.display().to_string())
                .replace("{properties}", &self.input.properties.display().to_string())
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
                    "{report}",
                    &serde_json::to_string_pretty(report).expect("the report serializes"),
                ),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stage() -> FormalizeStage {
        FormalizeStage {
            input: FormalizeInput {
                extracted: PathBuf::from("/c/lusterna/lean/Extraction"),
                lean_root: PathBuf::from("/c/lusterna/lean"),
                properties: PathBuf::from("/c/lusterna/properties.json"),
            },
            properties: Properties(Vec::new()),
        }
    }

    #[test]
    fn briefing_fills_every_placeholder() {
        let briefing = stage().briefing();

        for placeholder in ["{lean_root}", "{extracted}", "{properties}"] {
            assert!(!briefing.contains(placeholder), "{placeholder} is left");
        }
        assert!(briefing.contains("`/c/lusterna/properties.json`"));
    }

    #[test]
    fn judge_prompt_fills_every_placeholder() {
        let report = FormalizeOutput {
            property_status: BTreeMap::from([(
                1,
                PropertyStatus::Skipped(SkipReason::Other("{lean_root}".to_owned())),
            )]),
            mutating_operations: BTreeMap::new(),
        };
        let prompt = stage().judge_prompt(&report, &report).unwrap();

        for placeholder in [
            "{extracted}",
            "{properties}",
            "{briefing}",
            "{skills}",
            "{report}",
        ] {
            assert!(!prompt.contains(placeholder), "{placeholder} is left");
        }
        assert!(prompt.contains("\"Other\": \"{lean_root}\""));
        assert!(prompt.contains("name: aeneas-specs"));
    }

    #[test]
    fn base_prompt_names_lean_root() {
        assert!(stage().base_prompt().contains("`/c/lusterna/lean`"));
    }

    #[test]
    fn skill_has_front_matter() {
        let skills = stage().skills();

        assert_eq!(skills.len(), 1);
        assert_eq!(skills[0].0, "aeneas-specs");
        assert!(skills[0].1.starts_with("---\nname: aeneas-specs\n"));
    }
}
