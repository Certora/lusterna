/* Template Views */

use std::{collections::BTreeMap, path::PathBuf};

use serde::Serialize;

use crate::stage::{
    formalize::{FormalizeOutput, Induction, PropertyFormalizationStatus, SkipReason},
    pre_translate::PreTranslateSubmission,
    properties::{Properties, Property, PropertyId, PropertyKind},
    prove::{
        AdditionalAssumption, AssumptionPurpose, PropertyProofStatus, ProveOutput, TheoremStatus,
    },
    translate::TranslateSubmission,
};

#[derive(Serialize)]
struct PropertyView {
    pub id: PropertyId,
    pub title: String,
    /// The natspec of the property
    pub natspec: String,
    /// If this property is an invariant, this option names the struct.
    /// If this is some, then `fns` must be empty
    pub struct_name: Option<String>,
    /// If this property is a simple rule, it names the functions
    /// If this is nonempty, `struct_name` must be `None`
    pub fns: Vec<String>,
    pub status: PropertyStatusView,
    /// The spec file of the formalization, relative to the Lean project root.
    /// `None` if the property was skipped or the formalize stage did not run.
    pub file: Option<PathBuf>,
    /// Number of theorems this property is formalized in. Must be be 0 if
    /// `skip_reason` is given.
    pub total_thms: usize,
    /// Number of theorems of this property that are proven, with or without
    /// added assumptions. Must be at most `total_thms`
    pub proven_thms: usize,
    /// The reason this property was skipped during formalization.
    /// If this is not `None` then the following must hold
    ///
    /// - `total_thms = proven_thms = 0`
    /// - `status = Skipped`
    pub skip_reason: Option<SkipReasonView>,
    /// The theorems in which this property was formalized.
    /// The following must hold:
    ///
    /// - `total_thms = theorems.len()`
    /// - `proven_thms = theorems.iter().filter(|t| t.is_proven()).count()`
    pub theorems: Vec<TheoremsView>,
}

#[derive(Serialize)]
struct SkipReasonView {
    pub kind: SkipKindView,
    /// The items missing from the translation, or the reason that the
    /// `SkipReason` gives
    pub details: String,
}

#[derive(Serialize)]
enum SkipKindView {
    #[serde(rename = "Missing translation")]
    MissingTranslation,
    /// The property itself is ill-stated and cannot be formalized.
    /// This does **not** mean it is violated or unprovable, but
    /// that it simply is not expressible.
    #[serde(rename = "Ill-stated")]
    IllStated,
    /// Something else
    #[serde(rename = "Other")]
    Other,
}

#[derive(Serialize, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum PropertyStatusView {
    #[serde(rename = "Proven")]
    Proven,
    #[serde(rename = "Proven with assumptions")]
    ProvenWithAssumptions,
    #[serde(rename = "Open")]
    Open,
    #[serde(rename = "Dropped write")]
    DroppedWrite,
    #[serde(rename = "Refuted")]
    Refuted,
    /// The formalize stage skipped the property
    #[serde(rename = "Skipped")]
    Skipped,
    /// The prove stage did not run
    #[serde(rename = "Not run")]
    NotRun,
}

#[derive(Serialize)]
struct TheoremsView {
    /// Lean FQN of the theorem
    pub name: String,
    pub status: TheoremStatusView,
    /// The reason of a refuted, open, exception or dropped write status.
    pub reason: Option<String>,
    /// If the status is a dropped write, the Lean FQN of the function that
    /// writes through the `RefMut`.
    pub dropped_write_function: Option<String>,
    /// The hypotheses that the prove stage added to the theorem.
    pub assumptions: Vec<AssumptionView>,
    /// If the theorem is proven and its proof goes through writes through a
    /// `core.cell.RefMut` that the translation drops: the writes, and why the
    /// conclusion does not depend on them.
    pub dropped_writes: Option<String>,
}

#[derive(Serialize, Clone, Copy)]
enum TheoremStatusView {
    #[serde(rename = "Proven")]
    Proven,
    #[serde(rename = "Proven with assumptions")]
    ProvenWithAssumptions,
    #[serde(rename = "Open")]
    Open,
    #[serde(rename = "Dropped write")]
    DroppedWrite,
    #[serde(rename = "Refuted")]
    Refuted,
    #[serde(rename = "Exception")]
    Exception,
    /// The prove stage did not run
    #[serde(rename = "Not run")]
    NotRun,
}

#[derive(Serialize)]
struct AssumptionView {
    /// The name of the hypothesis on the Lean theorem
    pub name: String,
    pub purpose: AssumptionPurposeView,
    /// Why the hypothesis was added
    pub reason: String,
    /// The fields of the `AssumptionPurpose` as text: the base cases and
    /// induction steps of a state invariant, the justification of an external
    /// assumption, the natspec quote, or the callers of a caller precondition.
    pub justification: String,
}

#[derive(Serialize, Clone, Copy)]
enum AssumptionPurposeView {
    #[serde(rename = "state invariant")]
    StateInvariant,
    #[serde(rename = "external")]
    External,
    #[serde(rename = "natspec")]
    Natspec,
    #[serde(rename = "caller precondition")]
    CallerPrecondition,
}

#[derive(Serialize, Clone)]
struct InductionView {
    /// The struct name, as the invariant properties give it
    pub name: String,
    /// Lean FQNs of the functions that create the struct
    pub base_cases: Vec<String>,
    /// Lean FQNs of the mutating operations of the struct
    pub steps: Vec<String>,
    pub unmodelled_steps: Vec<UnmodelledStepView>,
    pub helpers: Vec<HelperView>,
}

#[derive(Serialize, Clone)]
struct UnmodelledStepView {
    /// Lean FQN of the operation
    pub function: String,
    /// The value of the struct that the operation changes, and the write that
    /// the translation drops
    pub write: String,
}

#[derive(Serialize, Clone)]
struct HelperView {
    /// Lean FQN of the helper
    pub function: String,
    /// Lean FQNs of the functions that call the helper
    pub callers: Vec<String>,
}

#[derive(Serialize)]
pub(super) struct ReportView {
    crate_name: String,
    properties: Vec<PropertyView>,
    inductions: Vec<InductionView>,
    /// `None` if the stage was skipped
    pre_translate: Option<PreTranslateSubmission>,
    /// `None` if the stage was skipped
    translate: Option<TranslateSubmission>,
    /// `None` if the stage was skipped
    formalize: Option<FormalizeOutput>,
}

impl ReportView {
    pub(super) fn new(
        crate_name: String,
        properties: &Properties,
        pre_translate_submission: Option<PreTranslateSubmission>,
        translate_submission: Option<TranslateSubmission>,
        formalize_submission: Option<FormalizeOutput>,
        prove_output: Option<&ProveOutput>,
    ) -> Self {
        let mut property_views = Vec::new();
        for property in &properties.0 {
            property_views.push(PropertyView::new(
                property,
                formalize_submission.as_ref(),
                prove_output,
            ));
        }

        let mut inductions = Vec::new();
        if let Some(formalize) = &formalize_submission {
            for (name, induction) in &formalize.induction {
                inductions.push(InductionView::new(name, induction));
            }
        }

        Self {
            crate_name,
            properties: property_views,
            inductions,
            pre_translate: pre_translate_submission,
            translate: translate_submission,
            formalize: formalize_submission,
        }
    }
}

impl PropertyView {
    fn new(
        property: &Property,
        formalize: Option<&FormalizeOutput>,
        prove: Option<&ProveOutput>,
    ) -> Self {
        let (struct_name, fns) = match &property.kind {
            PropertyKind::Rule { fns } => (None, fns.clone()),
            PropertyKind::Invariant { r#struct } => (Some(r#struct.clone()), Vec::new()),
        };

        let proof = prove.and_then(|prove| prove.properties.get(&property.id));
        let (file, skip_reason, theorems) =
            match formalize.and_then(|formalize| formalize.property_status.get(&property.id)) {
                Some(PropertyFormalizationStatus::Formalized { file, theorems, .. }) => {
                    (Some(file.clone()), None, theorem_views(theorems, proof))
                }
                Some(PropertyFormalizationStatus::Skipped(reason)) => {
                    (None, Some(SkipReasonView::from(reason)), Vec::new())
                }
                None => (None, None, Vec::new()),
            };

        let status = match skip_reason {
            Some(_) => PropertyStatusView::Skipped,
            None => property_status(&theorems),
        };
        let proven_thms = theorems
            .iter()
            .filter(|t| {
                matches!(
                    t.status,
                    TheoremStatusView::Proven | TheoremStatusView::ProvenWithAssumptions
                )
            })
            .count();

        Self {
            id: property.id,
            title: property.title.clone(),
            natspec: property.natspec.clone(),
            struct_name,
            fns,
            status,
            file,
            total_thms: theorems.len(),
            proven_thms,
            skip_reason,
            theorems,
        }
    }
}

/// Returns a view of each theorem of `names`, with its status in `proof`.
fn theorem_views(names: &[String], proof: Option<&PropertyProofStatus>) -> Vec<TheoremsView> {
    let statuses = proof.map(theorem_statuses).unwrap_or_default();
    let mut views = Vec::new();
    for name in names {
        views.push(TheoremsView::new(
            name,
            statuses.get(name.as_str()).copied(),
        ));
    }
    views
}

/// Returns the status of each theorem of `proof`, by theorem name.
fn theorem_statuses(proof: &PropertyProofStatus) -> BTreeMap<&str, &TheoremStatus> {
    let mut statuses = BTreeMap::new();
    match proof {
        PropertyProofStatus::Rule { theorems } => {
            for (name, status) in theorems {
                statuses.insert(name.as_str(), status);
            }
        }
        PropertyProofStatus::Induction { base_cases, steps } => {
            for (name, status) in base_cases.values().chain(steps.values()).flatten() {
                statuses.insert(name.as_str(), status);
            }
        }
    }
    statuses
}

/// Returns the worst status of `theorems`, leaving out exceptions. `NotRun`
/// if no theorem is left.
fn property_status(theorems: &[TheoremsView]) -> PropertyStatusView {
    let mut worst = None;
    for theorem in theorems {
        let status = match theorem.status {
            TheoremStatusView::Proven => PropertyStatusView::Proven,
            TheoremStatusView::ProvenWithAssumptions => PropertyStatusView::ProvenWithAssumptions,
            TheoremStatusView::Open => PropertyStatusView::Open,
            TheoremStatusView::DroppedWrite => PropertyStatusView::DroppedWrite,
            TheoremStatusView::Refuted => PropertyStatusView::Refuted,
            TheoremStatusView::Exception => continue,
            TheoremStatusView::NotRun => PropertyStatusView::NotRun,
        };
        worst = worst.max(Some(status));
    }
    worst.unwrap_or(PropertyStatusView::NotRun)
}

impl TheoremsView {
    /// `status` is `None` if the prove stage did not run.
    fn new(name: &str, status: Option<&TheoremStatus>) -> Self {
        let (reason, dropped_write_function, assumptions) = match status {
            Some(
                TheoremStatus::Refuted { reason }
                | TheoremStatus::Open { reason }
                | TheoremStatus::Exception { reason },
            ) => (Some(reason.clone()), None, Vec::new()),
            Some(TheoremStatus::DroppedWrite { function, reason }) => {
                (Some(reason.clone()), Some(function.clone()), Vec::new())
            }
            Some(TheoremStatus::ProvenWithAssumptions { assumptions }) => (
                None,
                None,
                assumptions.iter().map(AssumptionView::from).collect(),
            ),
            Some(TheoremStatus::Proven) | None => (None, None, Vec::new()),
        };

        Self {
            name: name.to_owned(),
            status: status.map_or(TheoremStatusView::NotRun, TheoremStatusView::from),
            reason,
            dropped_write_function,
            assumptions,
            dropped_writes: None,
        }
    }
}

impl From<&TheoremStatus> for TheoremStatusView {
    fn from(status: &TheoremStatus) -> Self {
        match status {
            TheoremStatus::Proven => Self::Proven,
            TheoremStatus::ProvenWithAssumptions { .. } => Self::ProvenWithAssumptions,
            TheoremStatus::Refuted { .. } => Self::Refuted,
            TheoremStatus::Open { .. } => Self::Open,
            TheoremStatus::Exception { .. } => Self::Exception,
            TheoremStatus::DroppedWrite { .. } => Self::DroppedWrite,
        }
    }
}

impl From<&SkipReason> for SkipReasonView {
    fn from(reason: &SkipReason) -> Self {
        let (kind, details) = match reason {
            SkipReason::MissingTranslation { items } => {
                (SkipKindView::MissingTranslation, code_list(items))
            }
            SkipReason::IllStated { reason } => (SkipKindView::IllStated, reason.clone()),
            SkipReason::Other(reason) => (SkipKindView::Other, reason.clone()),
        };
        Self { kind, details }
    }
}

impl From<&AdditionalAssumption> for AssumptionView {
    fn from(assumption: &AdditionalAssumption) -> Self {
        let (purpose, justification) = match &assumption.purpose {
            AssumptionPurpose::StateInvariant {
                base_cases,
                induction_steps,
            } => (
                AssumptionPurposeView::StateInvariant,
                format!(
                    "Base cases: {}. Induction steps: {}.",
                    code_list(base_cases),
                    code_list(induction_steps)
                ),
            ),
            AssumptionPurpose::External { justification } => {
                (AssumptionPurposeView::External, justification.clone())
            }
            AssumptionPurpose::Natspec { quote } => {
                (AssumptionPurposeView::Natspec, format!("\"{quote}\""))
            }
            AssumptionPurpose::CallerPrecondition { callers } => {
                let callers: Vec<String> = callers
                    .iter()
                    .map(|(caller, condition)| format!("`{caller}`: {condition}"))
                    .collect();
                (
                    AssumptionPurposeView::CallerPrecondition,
                    callers.join("; "),
                )
            }
        };

        Self {
            name: assumption.name.clone(),
            purpose,
            reason: assumption.reason.clone(),
            justification,
        }
    }
}

/// Returns `items` as inline code, separated by commas.
fn code_list(items: &[String]) -> String {
    let items: Vec<String> = items.iter().map(|item| format!("`{item}`")).collect();
    items.join(", ")
}

impl InductionView {
    /// `name` is the struct of `induction`.
    fn new(name: &str, induction: &Induction) -> Self {
        let mut unmodelled_steps = Vec::new();
        for (function, write) in &induction.unmodelled_steps {
            unmodelled_steps.push(UnmodelledStepView {
                function: function.clone(),
                write: write.clone(),
            });
        }
        let mut helpers = Vec::new();
        for (function, callers) in &induction.helpers {
            helpers.push(HelperView {
                function: function.clone(),
                callers: callers.clone(),
            });
        }

        Self {
            name: name.to_owned(),
            base_cases: induction.base_cases.clone(),
            steps: induction.steps.clone(),
            unmodelled_steps,
            helpers,
        }
    }
}
