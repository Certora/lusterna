mod gate;

use std::{collections::BTreeMap, fs, io, path::PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    stage::{
        INDUCTION_SKILL, INVARIANTS_SKILL, Stage, StageName,
        formalize::{Induction, PropertyFormalizationStatus},
        properties::{Properties, PropertyId},
    },
    utils::git::{self, GitError},
};

pub use gate::{ProveGateError, ProveGateFailure};

const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

const BRIEFING_TEMPLATE: &str = include_str!("res/briefing.md");

const JUDGE_PROMPT_TEMPLATE: &str = include_str!("res/judge.md");

/// The `SKILL.md` of the skill `aeneas-tactics`.
const AENEAS_TACTICS_SKILL: &str = include_str!("res/aeneas-tactics.md");

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
    /// For each struct that an invariant property is declared on, keyed by the
    /// struct name as the property gives it, the functions of its base cases
    /// and induction steps.
    pub induction: BTreeMap<String, Induction>,
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
    /// Still `sorry`: false as stated, and no assumption that the briefing
    /// allows makes it provable.
    Refuted {
        /// An explanation of a counterexample that follows the Rust code: the
        /// values of the inputs and of the state fields that the hypotheses
        /// and the conclusion read, which satisfy the hypotheses; each branch
        /// taken and each value computed on the way to the result; why the
        /// conclusion fails for the result; the invariants and caller
        /// conditions that were checked and do not rule it out; and the goal
        /// at which the proof attempt stopped.
        reason: String,
    },
    /// Still `sorry`.
    Open { reason: String },
    /// Still `sorry`: the natspec names the function of the theorem as an
    /// exception to the invariant.
    Exception {
        /// The reason, as the natspec gives it.
        reason: String,
    },
    /// Neither proven nor refuted: the proof holds only because the
    /// translation drops a write through a `core.cell.RefMut` that the
    /// conclusion depends on.
    DroppedWrite {
        /// The full Lean name of the function that writes through the
        /// `RefMut`.
        function: String,
        /// The type behind the `RefMut`, and how the conclusion depends on
        /// the write.
        reason: String,
    },
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
    /// Assumes another invariant that holds in the pre-state.
    /// Must supply
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
    /// A condition on the arguments that every caller of the function of the
    /// theorem establishes.
    CallerPrecondition {
        /// Each function of the translation that calls the function of the
        /// theorem, by its full Lean name, with where it establishes the
        /// condition.
        callers: BTreeMap<String, String>,
    },
}

/// Report from the agent once done with proving

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ProveReport {
    /// The theorems (fully qualified lean name) and their proof status
    pub theorems: BTreeMap<String, TheoremStatus>,
    /// For each theorem that the report claims to be proven and that depends
    /// on `Lusterna.refMutWriteDropped`: the writes through a
    /// `core.cell.RefMut` that its proof goes through, and why its conclusion
    /// does not depend on them.
    pub dropped_writes: BTreeMap<String, String>,
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

#[derive(Debug, thiserror::Error)]
pub enum ProveSetupError {
    #[error("the work tree of `{0}` has uncommitted changes")]
    Dirty(PathBuf),
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error(transparent)]
    Git(#[from] GitError),
    #[error("cannot parse the properties in `{0}`: {1}")]
    Properties(PathBuf, serde_json::Error),
}

#[derive(Deserialize, Serialize)]
pub struct ProveStage {
    input: ProveInput,
    /// The content of `input.properties`.
    properties: Properties,
}

impl Stage for ProveStage {
    const NAME: super::StageName = StageName::Prove;

    type Input = ProveInput;

    type Output = ProveOutput;

    type Report = ProveReport;

    type Blocker = ProveBlocker;

    type GateFailure = ProveGateFailure;

    type GateExecutionError = ProveGateError;

    type SetupError = ProveSetupError;

    fn setup(input: Self::Input) -> Result<Self, Self::SetupError> {
        if !git::is_clean(&input.lean_root)? {
            return Err(ProveSetupError::Dirty(input.lean_root));
        }
        let properties = serde_json::from_str(&fs::read_to_string(&input.properties)?)
            .map_err(|e| ProveSetupError::Properties(input.properties.clone(), e))?;
        Ok(Self { input, properties })
    }

    fn base_prompt(&self) -> String {
        BASE_PROMPT_TEMPLATE.replace("{lean_root}", &self.input.lean_root.display().to_string())
    }

    fn briefing(&self) -> String {
        let induction =
            serde_json::to_string_pretty(&self.input.induction).expect("the induction serializes");
        // The induction steps that the formalize stage excepted, with their reasons
        let mut exceptions = BTreeMap::new();
        for status in self.input.property_status.values() {
            if let PropertyFormalizationStatus::Formalized {
                exceptions: property_exceptions,
                ..
            } = status
            {
                exceptions.extend(property_exceptions);
            }
        }
        let exceptions =
            serde_json::to_string_pretty(&exceptions).expect("the exceptions serialize");
        BRIEFING_TEMPLATE
            .replace("{lean_root}", &self.input.lean_root.display().to_string())
            .replace("{extracted}", &self.input.extracted.display().to_string())
            .replace("{properties}", &self.input.properties.display().to_string())
            .replace("{induction}", &induction)
            .replace("{exceptions}", &exceptions)
    }

    fn skills(&self) -> Vec<(&'static str, String)> {
        vec![
            ("aeneas-tactics", AENEAS_TACTICS_SKILL.to_owned()),
            ("induction", INDUCTION_SKILL.to_owned()),
            ("invariants", INVARIANTS_SKILL.to_owned()),
        ]
    }

    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String {
        gate_failed.to_feedback_prompt()
    }

    fn gate_check(&self, report: &Self::Report) -> Result<Self::Output, super::GateStop<Self>> {
        gate::run_gate(&self.input, &self.properties, report)
    }

    fn judge_prompt(&self, _output: &Self::Output, report: &Self::Report) -> Option<String> {
        let report = serde_json::to_string_pretty(report).expect("the report serializes");
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace("{lean_root}", &self.input.lean_root.display().to_string())
                .replace("{extracted}", &self.input.extracted.display().to_string())
                .replace("{properties}", &self.input.properties.display().to_string())
                .replace("{invariants}", INVARIANTS_SKILL)
                .replace("{briefing}", &self.briefing())
                .replace("{report}", &report),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stage() -> ProveStage {
        ProveStage {
            input: ProveInput {
                extracted: PathBuf::from("/c/lusterna/lean/Extraction"),
                lean_root: PathBuf::from("/c/lusterna/lean"),
                properties: PathBuf::from("/c/lusterna/properties.json"),
                property_status: BTreeMap::new(),
                induction: BTreeMap::new(),
            },
            properties: Properties(Vec::new()),
        }
    }

    #[test]
    fn briefing_fills_every_placeholder() {
        let briefing = stage().briefing();

        for placeholder in [
            "{lean_root}",
            "{extracted}",
            "{properties}",
            "{induction}",
            "{exceptions}",
        ] {
            assert!(!briefing.contains(placeholder), "{placeholder} is left");
        }
        assert!(briefing.contains("`/c/lusterna/properties.json`"));
    }

    #[test]
    fn judge_prompt_fills_every_placeholder() {
        let report = ProveReport {
            theorems: BTreeMap::from([(
                "Spec.FAdds.f_adds".to_owned(),
                TheoremStatus::Open {
                    reason: "{lean_root}".to_owned(),
                },
            )]),
            dropped_writes: BTreeMap::new(),
        };
        let output = ProveOutput {
            properties: BTreeMap::new(),
        };
        let prompt = stage().judge_prompt(&output, &report).unwrap();

        for placeholder in [
            "{extracted}",
            "{properties}",
            "{invariants}",
            "{briefing}",
            "{report}",
        ] {
            assert!(!prompt.contains(placeholder), "{placeholder} is left");
        }
        assert!(prompt.contains("\"reason\": \"{lean_root}\""));
    }

    #[test]
    fn base_prompt_names_lean_root() {
        assert!(stage().base_prompt().contains("`/c/lusterna/lean`"));
    }

    #[test]
    fn skill_has_front_matter() {
        for (name, content) in stage().skills() {
            assert!(content.starts_with(&format!("---\nname: {name}\n")));
        }
    }
}
