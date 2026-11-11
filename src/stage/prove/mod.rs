use std::{collections::BTreeMap, io, path::PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    stage::{Stage, StageName, formalize::PropertyFormalizationStatus, properties::PropertyId},
    utils::{
        git::{self, GitError},
        lean::LakeError,
    },
};

const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

const BRIEFING_TEMPLATE: &str = include_str!("res/briefing.md");

// const JUDGE_PROMPT_TEMPLATE: &str = todo!();

#[derive(Debug, Serialize, Deserialize)]
pub struct ProveInput {
    /// Absolute path to the directory of the Lean modules that Aeneas
    /// generated
    pub extracted: PathBuf,
    /// Path to the lean root, from which lake can be executed.
    pub lean_root: PathBuf,
    /// Absolute path to the JSON file of the properties
    pub properties: PathBuf,
    /// The formalization status per property.
    /// This stage will attempt to construct proves for all theorem for the
    /// properties with status `Formalized`
    pub property_status: BTreeMap<PropertyId, PropertyFormalizationStatus>,
    /// Additional theorems created by the formalize stage that are not tied to
    /// one property
    pub additional_theorem: Vec<(PathBuf, String)>,
}

/// The state a theorem is in
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub enum TheoremStatus {
    /// Proven as stated.
    Proven,
    /// Proven after adding these hypotheses under `-- Additional assumptions`.
    ProvenWithAssumptions {
        assumptions: Vec<AdditionalAssumption>,
    },
    /// False as stated and no reasonable additions of assumption can be added
    /// to prove it.
    Refuted {
        /// The FQN of a proven theorem: a state and inputs that satisfy the
        /// hypotheses of the theorem and for which its conclusion fails.
        counterexample: String,
        /// The FQN of a proven theorem: a run of the program, from a function
        /// that creates the state, to a state that violates the property.
        /// `None` if the agent found no such run.
        reachable: Option<String>,
    },
    /// Still `sorry`.
    Open { reason: String },
}

/// An additional assumption that was added to an existing theorem
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct AdditionalAssumption {
    /// The name of the hypothesis on the lean theorem
    pub name: String,
    /// The reason why it was added
    pub reason: String,
    /// The purpose of the added assumption, which includes its justification
    pub purpose: AssumptionPurpose,
}

/// The purpose of an added extra assumption
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub enum AssumptionPurpose {
    /// It states that the computation in the theorem's triple does not panic.
    NoPanic,
    /// Assumes another invariant that holds in the pre-state.
    /// Must supply
    /// - the property as stated as a Prop in lean which must match what the
    ///   assumption expresses
    /// - The FQNs of the (proven) base cases
    /// - The FQNs of the (proven) induction steps
    StateInvariant {
        base_cases: Vec<String>,
        induction_steps: Vec<String>,
    },
    /// It is about code outside the translation, which Lean cannot check.
    External { justification: String },
    /// The natspec states this condition, and the theorem lacks it. `quote` is
    /// the part of the natspec that states it, copied exactly.
    Natspec { quote: String },
}

/// Report from the agent once done with proving

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ProveReport {
    /// The theorems (fully qualified lean name) and their proof status
    pub theorems: BTreeMap<String, TheoremStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ProveOutput {
    pub properties: BTreeMap<PropertyId, PropertyProofStatus>,
}

/// A property proof groups together multiple theorem proofs that belong to one
/// property
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub enum PropertyProofStatus {
    Rule {
        theorems: BTreeMap<String, TheoremStatus>,
    },
    Induction {
        /// Bases cases mapping to the name of the corresponding theorem and its
        /// proof status
        base_cases: BTreeMap<String, Vec<(String, TheoremStatus)>>,
        /// Induction steps mapping to the name of the corresponding theorem and
        /// its proof status
        steps: BTreeMap<String, Vec<(String, TheoremStatus)>>,
    },
}

#[derive(Debug, derive_more::Display, JsonSchema, Serialize, Deserialize)]
#[display("{reason}")]
pub struct ProveBlocker {
    pub reason: String,
}

#[derive(Debug, derive_more::Display, Serialize, Deserialize, Clone)]
pub enum ProveGateFailure {
    #[display("Lean project does not build")]
    DoesNotBuild,
}

#[derive(Debug, thiserror::Error)]
pub enum ProveGateError {
    #[error("Error running git: {0}")]
    Git(#[from] GitError),
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("Lake error: {0}")]
    Lake(#[from] LakeError),
}

#[derive(Debug, thiserror::Error)]
pub enum ProveSetupError {
    #[error("the work tree of `{0}` has uncommitted changes")]
    Dirty(PathBuf),
    #[error(transparent)]
    Git(#[from] GitError),
}

#[derive(Deserialize, Serialize)]
struct ProveStage {
    input: ProveInput,
}

impl Stage for ProveStage {
    const NAME: super::StageName = StageName::Prove;

    type Input = ProveInput;

    type Output = PropertyProofStatus;

    type Report = ProveReport;

    type Blocker = ProveBlocker;

    type GateFailure = ProveGateFailure;

    type GateExecutionError = ProveGateError;

    type SetupError = ProveSetupError;

    fn setup(input: Self::Input) -> Result<Self, Self::SetupError> {
        if !git::is_clean(&input.lean_root)? {
            return Err(ProveSetupError::Dirty(input.lean_root));
        }
        Ok(Self { input })
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

    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String {
        todo!()
    }

    fn gate_check(&self, report: &Self::Report) -> Result<Self::Output, super::GateStop<Self>> {
        todo!()
    }
}
