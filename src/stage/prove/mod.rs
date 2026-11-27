mod gate;
#[cfg(test)]
mod utils;

use std::{collections::BTreeMap, io, path::PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    agent::AgentOptions,
    config::Config,
    stage::{
        INDUCTION_SKILL, INVARIANTS_SKILL, Stage, StageName,
        formalize::{Induction, PropertyFormalizationStatus},
        lean_agent_options,
        properties::{Properties, Property, PropertyId},
    },
    utils::{
        git::{self, GitError},
        md,
    },
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
    /// Absolute path to the root of the crate
    pub crate_root: PathBuf,
    /// The properties that the formalize stage formalized, by id. This stage
    /// tries to prove their theorems.
    pub formalized: BTreeMap<PropertyId, FormalizedProperty>,
    /// For each struct that an invariant property is declared on, keyed by the
    /// struct name as the property gives it, the functions of its base cases
    /// and induction steps.
    pub induction: BTreeMap<String, Induction>,
}

/// A property that the formalize stage formalized.
#[derive(Debug, Serialize, Deserialize)]
pub struct FormalizedProperty {
    pub property: Property,
    /// The spec file, relative to the Lean project root.
    pub file: PathBuf,
    /// The full Lean names of the theorems that express the property.
    pub theorems: Vec<String>,
    /// The induction steps whose operation the natspec names as an
    /// exception to the invariant, by full Lean name, each with the reason
    /// that the natspec gives.
    pub exceptions: BTreeMap<String, String>,
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
    /// Assumes an invariant for the state before the call.
    StateInvariant(AssumedInvariant),
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

/// The invariant that a `StateInvariant` assumption assumes.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub enum AssumedInvariant {
    /// An invariant property, by its id.
    Property(PropertyId),
    /// An invariant that the prove stage added, by its key in
    /// `additional_invariants`.
    Additional(String),
}

/// An invariant that was added by the prove stage to be used as a hypothesis.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct AddedInvariant {
    /// A short title of the invariant, in the style of the titles of the
    /// properties.
    pub title: String,
    /// What the invariant states, in plain English.
    pub natspec: String,
    /// The file of the invariant and its theorems, relative to the Lean
    /// project root.
    pub file: PathBuf,
    /// The struct of the invariant, as `induction` or `additional_inductions`
    /// names it.
    pub r#struct: String,
    /// The full Lean names of its base cases and induction steps.
    pub theorems: Vec<String>,
}

/// Submission from the agent once done with proving

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ProveSubmission {
    /// The theorems (fully qualified lean name) and their proof status
    pub theorems: BTreeMap<String, TheoremStatus>,
    /// For each theorem that the submission claims to be proven and that
    /// depends on `Lusterna.refMutWriteDropped`: the writes through a
    /// `core.cell.RefMut` that its proof goes through, and why its conclusion
    /// does not depend on them.
    pub dropped_writes: BTreeMap<String, String>,
    /// The invariants that the prove stage added, by the full Lean name of the
    /// invariant's `def`.
    pub additional_invariants: BTreeMap<String, AddedInvariant>,
    /// For each struct of an added invariant that has no entry in `induction`,
    /// keyed by the struct name, the functions of its base cases and induction
    /// steps.
    pub additional_inductions: BTreeMap<String, Induction>,
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
}

impl Stage for ProveStage {
    const NAME: super::StageName = StageName::Prove;

    type Input = ProveInput;

    type Submission = ProveSubmission;

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
        let induction =
            serde_json::to_string_pretty(&self.input.induction).expect("the induction serializes");
        // The induction steps that the formalize stage excepted, with their
        // reasons
        let mut exceptions = BTreeMap::new();
        for property in self.input.formalized.values() {
            exceptions.extend(&property.exceptions);
        }
        let exceptions =
            serde_json::to_string_pretty(&exceptions).expect("the exceptions serialize");
        BRIEFING_TEMPLATE
            .replace("{lean_root}", &self.input.lean_root.display().to_string())
            .replace("{extracted}", &self.input.extracted.display().to_string())
            .replace("{properties}", &self.input.properties.display().to_string())
            .replace("{crate_root}", &self.input.crate_root.display().to_string())
            .replace("{induction}", &induction)
            .replace("{exceptions}", &exceptions)
    }

    fn agent_options(&self, cfg: &Config) -> AgentOptions {
        lean_agent_options(cfg)
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

    fn gate_check(
        &self,
        submission: &Self::Submission,
    ) -> Result<Option<ProveGateFailure>, ProveGateError> {
        gate::run_gate(&self.input, submission)
    }

    fn judge_prompt(&self, submission: &Self::Submission) -> Option<String> {
        // `names` as a numbered list, under a line that gives their number
        let numbered_list = |names: Vec<&String>| {
            format!(
                "Number of entries: {}\n\n{}",
                names.len(),
                md::numbered_list(names.iter().map(md::code))
            )
        };
        // The theorems of the submission whose status `has_status` accepts
        let theorems_with = |has_status: fn(&TheoremStatus) -> bool| {
            let mut names = Vec::new();
            for (name, status) in &submission.theorems {
                if has_status(status) {
                    names.push(name);
                }
            }
            numbered_list(names)
        };
        // The entries of the submission that each section of the judge prompt
        // checks
        let with_assumptions =
            theorems_with(|s| matches!(s, TheoremStatus::ProvenWithAssumptions { .. }));
        let refuted = theorems_with(|s| matches!(s, TheoremStatus::Refuted { .. }));
        let exceptions = theorems_with(|s| matches!(s, TheoremStatus::Exception { .. }));
        let open = theorems_with(|s| matches!(s, TheoremStatus::Open { .. }));
        let dropped_write = theorems_with(|s| matches!(s, TheoremStatus::DroppedWrite { .. }));
        let dropped_writes = numbered_list(submission.dropped_writes.keys().collect());
        let added_invariants = numbered_list(submission.additional_invariants.keys().collect());
        let added_inductions = numbered_list(submission.additional_inductions.keys().collect());

        let submission =
            serde_json::to_string_pretty(submission).expect("the submission serializes");
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace("{lean_root}", &self.input.lean_root.display().to_string())
                .replace("{extracted}", &self.input.extracted.display().to_string())
                .replace("{properties}", &self.input.properties.display().to_string())
                .replace(
                    "{invariants}",
                    &format!("{INDUCTION_SKILL}\n\n{INVARIANTS_SKILL}"),
                )
                .replace("{theorems_with_assumptions}", &with_assumptions)
                .replace("{refuted_theorems}", &refuted)
                .replace("{exception_theorems}", &exceptions)
                .replace("{open_theorems}", &open)
                .replace("{dropped_write_theorems}", &dropped_write)
                .replace("{dropped_writes}", &dropped_writes)
                .replace("{added_invariants}", &added_invariants)
                .replace("{added_inductions}", &added_inductions)
                .replace("{briefing}", &self.briefing())
                .replace("{submission}", &submission),
        )
    }
}

/// Returns each property of `properties` that has the status `Formalized` in
/// `property_status`, by id, with the theorems of that status.
pub(crate) fn formalized_properties(
    properties: &Properties,
    mut property_status: BTreeMap<PropertyId, PropertyFormalizationStatus>,
) -> BTreeMap<PropertyId, FormalizedProperty> {
    let mut formalized = BTreeMap::new();
    for property in &properties.0 {
        // Skip a property that the formalize stage skipped
        let Some(PropertyFormalizationStatus::Formalized {
            file,
            theorems,
            exceptions,
        }) = property_status.remove(&property.id)
        else {
            continue;
        };
        formalized.insert(
            property.id,
            FormalizedProperty {
                property: property.clone(),
                file,
                theorems,
                exceptions,
            },
        );
    }
    formalized
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stage::{assert_no_placeholders, formalize::SkipReason, properties::PropertyKind};

    fn stage() -> ProveStage {
        ProveStage {
            input: ProveInput {
                extracted: PathBuf::from("/c/lusterna/lean/Extraction"),
                lean_root: PathBuf::from("/c/lusterna/lean"),
                properties: PathBuf::from("/c/lusterna/properties.json"),
                crate_root: PathBuf::from("/c"),
                formalized: BTreeMap::new(),
                induction: BTreeMap::new(),
            },
        }
    }

    /// A submission that leaves one theorem open with the reason `reason`
    fn submission(reason: &str) -> ProveSubmission {
        ProveSubmission {
            theorems: BTreeMap::from([(
                "Spec.FAdds.f_adds".to_owned(),
                TheoremStatus::Open {
                    reason: reason.to_owned(),
                },
            )]),
            dropped_writes: BTreeMap::new(),
            additional_invariants: BTreeMap::new(),
            additional_inductions: BTreeMap::new(),
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
        let prompt = stage().judge_prompt(&submission("no lemma")).unwrap();

        assert_no_placeholders(&prompt, &[]);
    }

    #[test]
    fn judge_prompt_lists_the_open_theorems() {
        let prompt = stage().judge_prompt(&submission("no lemma")).unwrap();

        assert!(prompt.contains(
            "The theorems with the status `Open`:\n\nNumber of entries: 1\n\n1. `Spec.FAdds.f_adds`"
        ));
        assert!(
            prompt.contains("The theorems with the status `Refuted`:\n\nNumber of entries: 0\n")
        );
    }

    #[test]
    fn judge_prompt_keeps_placeholders_in_the_submission() {
        let prompt = stage().judge_prompt(&submission("{lean_root}")).unwrap();

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

    #[test]
    fn formalized_properties_leaves_out_skipped_properties() {
        let property = |id| Property {
            id,
            kind: PropertyKind::Rule { fns: Vec::new() },
            title: String::new(),
            natspec: String::new(),
            locked: true,
        };
        let properties = Properties(vec![property(1), property(2)]);
        let property_status = BTreeMap::from([
            (
                1,
                PropertyFormalizationStatus::Formalized {
                    file: PathBuf::from("Spec/F.lean"),
                    theorems: vec!["Spec.F.f".to_owned()],
                    exceptions: BTreeMap::new(),
                },
            ),
            (
                2,
                PropertyFormalizationStatus::Skipped(SkipReason::Other(String::new())),
            ),
        ]);

        let formalized = formalized_properties(&properties, property_status);

        assert_eq!(formalized.keys().collect::<Vec<_>>(), [&1]);
        assert_eq!(formalized[&1].theorems, ["Spec.F.f"]);
    }
}
