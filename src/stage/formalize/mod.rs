pub(crate) mod properties;

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

#[derive(Deserialize, Serialize)]
pub struct FormalizeStage {
    input: FormalizeInput,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct FormalizeInput {
    /// Absolute path to the directory of the Lean modules that Aeneas
    /// generated
    pub extracted: PathBuf,
    /// Path to the lean root, from which lake can be executed.
    pub lean_root: PathBuf,
    /// The properties for formalizes
    pub properties: Properties,
}

#[derive(Debug, derive_more::Display, JsonSchema, Serialize, Deserialize)]
pub struct FormalizeBlocker;

#[derive(Debug, derive_more::Display, Serialize, Deserialize, Clone)]
pub enum FormalizeGateFailure {}

#[derive(Debug, thiserror::Error, Serialize, Deserialize, Clone)]
pub enum FormalizeGateError {}

#[derive(Debug, thiserror::Error)]
pub enum FormalizeSetupError {
    #[error("the work tree of `{0}` has uncommitted changes")]
    Dirty(PathBuf),
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error(transparent)]
    Git(#[from] GitError),
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
        file: PathBuf,
        /// The theorems that express the property.
        theorems: Vec<String>,
    },
    Skipped(SkipReason),
}

#[derive(Debug, Clone, JsonSchema, Serialize, Deserialize)]
pub struct FormalizeOutput {
    pub property_status: BTreeMap<PropertyId, PropertyStatus>,
    /// For each struct that an invariant property is declared on, the
    /// functions that mutate it.
    pub mutators: BTreeMap<String, Vec<String>>,
}

#[derive(Debug, derive_more::Display, Serialize, Deserialize)]
pub enum FormalizeGateExecutionError {}

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
        fs::create_dir_all(input.lean_root.join("Spec"))?;
        Ok(Self { input })
    }

    fn base_prompt(&self) -> String {
        todo!()
    }

    fn briefing(&self) -> String {
        todo!()
    }

    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String {
        todo!()
    }

    fn gate_check(&self, report: &Self::Report) -> Result<Self::Output, super::GateStop<Self>> {
        todo!()
    }
}
