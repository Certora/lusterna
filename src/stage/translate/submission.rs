//! The result types of the translate agent: its submission, and the reasons for
//! which it stops the stage.

use std::fmt;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Every change that the agent made since the start of the stage.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct TranslateSubmission {
    /// Each change to `lusterna/extract-args.sh`.
    pub extract_changes: Vec<ExtractChange>,
    /// Each change to the Rust code, including each replaced logging macro.
    pub rust_changes: Vec<RustChange>,
    /// Each definition in `FunsExternal.lean` and `TypesExternal.lean` that
    /// replaces an `axiom`.
    pub lean_definitions: Vec<LeanDefinition>,
    /// Each `axiom` that stays in `FunsExternal.lean` or `TypesExternal.lean`.
    pub kept_axioms: Vec<KeptAxiom>,
    /// Each opaque pattern that matches nothing.
    pub unmatched_opaque_patterns: Vec<String>,
    /// Each fact that a skill tells the agent to report, for example the
    /// entrypoints that write through a loader.
    pub notes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ExtractChange {
    /// What changed, for example an added option.
    pub change: String,
    /// Why.
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct RustChange {
    /// The original file that changed, relative to the crate root, not its
    /// file under `src/lusterna/`. A change to several files has one entry per
    /// file. For a replacement of a type from a dependency, the file under
    /// `src/lusterna/deps/`.
    pub file: String,
    /// The Rust items that changed, each one function, method, macro or type,
    /// by its path, without backticks. `replaced` says which part of an item
    /// changed, if not all of it.
    pub items: Vec<String>,
    /// What the agent replaced, and with what.
    pub replaced: String,
    /// Why Charon or Aeneas failed on the original.
    pub failure: String,
    /// The name of the equivalence test, or `null` for a change that needs
    /// none, such as a replaced logging macro.
    pub test: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct LeanDefinition {
    /// The Lean name.
    pub name: String,
    /// The Rust item that the definition models.
    pub rust_item: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct KeptAxiom {
    /// The Lean name.
    pub name: String,
    /// The opaque pattern that the item matches.
    pub opaque_pattern: String,
}

/// A reason for which the agent stops the translate stage.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TranslateBlocker {
    /// A stub of `lusterna-stubs` does not have an item that the Charon build
    /// needs.
    MissingStubItem {
        /// The crate of the stub.
        #[serde(rename = "crate")]
        krate: String,
        /// The missing item.
        item: String,
        /// The crate and the file that use the item.
        used_by: String,
    },
}

impl fmt::Display for TranslateBlocker {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TranslateBlocker::MissingStubItem {
                krate,
                item,
                used_by,
            } => write!(
                f,
                "the stub of `{krate}` in `lusterna-stubs` does not have `{item}`, which {used_by} uses"
            ),
        }
    }
}
