//! The gate of the prove stage. It checks the work of the agent in this order:
//!
//! 1. Only files under `Spec/` differ from `HEAD`.
//! 2. `lake build Spec` passes.
//! 3. Every theorem that the report names is a theorem under `Spec/`.
//! 4. Every theorem that the report claims to be proven is proven.
//! 5. Every labelled theorem and every theorem that is not proven has a status
//!    in the report.
//! 6. Every theorem that the report claims to be proven and that depends on
//!    `Lusterna.refMutWriteDropped` has an entry in its `dropped_writes`.
//!
//! Then it groups the statuses of the report by property.
//!
//! Labelled theorem: a theorem tagged `@[lusterna id "title"]`, which states
//! the property with that `id`.
//!
//! A theorem is proven if `Lusterna.Spec.check` reports no axioms for it.
//!
//! The report claims that a theorem is proven if it
//! - gives the theorem the status `Proven` or `ProvenWithAssumptions`,
//! - names it as the `counterexample` or the `reachable` of a `Refuted`
//!   theorem, or
//! - names it in the `base_cases` or the `induction_steps` of a
//!   `StateInvariant` assumption.

use std::{
    collections::{BTreeMap, BTreeSet},
    io,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    stage::{
        self,
        formalize::PropertyFormalizationStatus,
        properties::{Properties, PropertyId, PropertyKind},
        prove::{
            AssumptionPurpose, PropertyProofStatus, ProveInput, ProveOutput, ProveReport,
            ProveStage, TheoremStatus,
        },
    },
    utils::{
        git::{self, GitError},
        lean::{self, LakeError},
        spec_check::{self, SpecCheckError, TheoremCheck},
    },
};

#[derive(Debug, thiserror::Error)]
pub enum ProveGateError {
    #[error("Error running git: {0}")]
    Git(#[from] GitError),
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("Lake error: {0}")]
    Lake(#[from] LakeError),
    #[error(transparent)]
    Check(#[from] SpecCheckError),
}

#[derive(Debug, derive_more::Display, Serialize, Deserialize, Clone)]
pub enum ProveGateFailure {
    #[display("{} files changed outside Spec/", files.len())]
    ChangedOutsideSpec { files: Vec<PathBuf> },
    #[display("lake build Spec failed")]
    BuildFailed { output: String },
    #[display("the report names {} theorems that do not exist", names.len())]
    UnknownTheorems { names: Vec<String> },
    /// Each theorem with the axioms that it depends on.
    #[display("{} theorems are not proven", theorems.len())]
    NotProven {
        theorems: Vec<(String, Vec<String>)>,
    },
    #[display("{} theorems have no status", theorems.len())]
    MissingStatus { theorems: Vec<String> },
    #[display("{} theorems drop writes without an explanation", theorems.len())]
    UnexplainedDroppedWrites { theorems: Vec<String> },
}

type GateStop = stage::GateStop<ProveStage>;

impl From<ProveGateFailure> for GateStop {
    fn from(failure: ProveGateFailure) -> Self {
        GateStop::Failure(failure)
    }
}

impl<E> From<E> for GateStop
where
    ProveGateError: From<E>,
{
    fn from(error: E) -> Self {
        GateStop::Error(error.into())
    }
}

/// Runs the gate checks on the work of the agent in the Lean project at
/// `input.lean_root`, and stops at the first check that fails. If all checks
/// pass, returns the statuses of `report`, grouped by property.
pub(super) fn run_gate(
    input: &ProveInput,
    properties: &Properties,
    report: &ProveReport,
) -> Result<ProveOutput, GateStop> {
    check_only_spec_changed(&input.lean_root)?;
    check_spec_builds(&input.lean_root)?;

    // What Lean reports about each theorem under `Spec/`
    let spec_theorems = spec_check::run(&input.lean_root, true)?;
    check_report(report, &spec_theorems)?;

    Ok(group_by_property(input, properties, report, &spec_theorems))
}

/// Fails if a file outside `Spec/` differs from `HEAD`.
fn check_only_spec_changed(lean_root: &Path) -> Result<(), GateStop> {
    let spec = lean_root.join("Spec").canonicalize()?;
    let files = git::uncommitted_files_outside(lean_root, &spec)?;
    if !files.is_empty() {
        return Err(ProveGateFailure::ChangedOutsideSpec { files }.into());
    }
    Ok(())
}

/// Fails if `lake build Spec` fails.
fn check_spec_builds(lean_root: &Path) -> Result<(), GateStop> {
    match lean::lake_build(lean_root, &["Spec"]) {
        Ok(()) => Ok(()),
        // The build ran and failed. The agent can fix this.
        Err(LakeError::Build { output, .. }) => {
            Err(ProveGateFailure::BuildFailed { output }.into())
        }
        // `lake` could not run. The agent cannot fix this.
        Err(error) => Err(error.into()),
    }
}

/// Checks `report` against `spec_theorems`, the theorems under `Spec/`, and
/// returns the first check that fails.
fn check_report(
    report: &ProveReport,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let claimed_proven = theorems_claimed_proven(report);
    check_names_exist(report, &claimed_proven, spec_theorems)?;
    check_claims_are_proven(&claimed_proven, spec_theorems)?;
    check_statuses_are_complete(report, spec_theorems)?;
    check_dropped_writes_explained(report, &claimed_proven, spec_theorems)
}

/// Returns the theorem with the full Lean name `name`.
fn find_theorem<'a>(spec_theorems: &'a [TheoremCheck], name: &str) -> Option<&'a TheoremCheck> {
    spec_theorems.iter().find(|theorem| theorem.name == name)
}

/// Returns whether `Lusterna.Spec.check` reports an empty list of axioms for
/// `theorem`.
fn is_proven(theorem: &TheoremCheck) -> bool {
    match &theorem.axioms {
        Some(axioms) => axioms.is_empty(),
        None => false,
    }
}

/// Returns the full Lean names of the theorems that `report` claims to be
/// proven.
fn theorems_claimed_proven(report: &ProveReport) -> BTreeSet<&String> {
    let mut claimed_proven = BTreeSet::new();
    for (name, status) in &report.theorems {
        match status {
            // The theorem itself
            TheoremStatus::Proven => {
                claimed_proven.insert(name);
            }
            // The theorem itself, and the base cases and induction steps of
            // each invariant that it assumes
            TheoremStatus::ProvenWithAssumptions { assumptions } => {
                claimed_proven.insert(name);
                for assumption in assumptions {
                    if let AssumptionPurpose::StateInvariant {
                        base_cases,
                        induction_steps,
                    } = &assumption.purpose
                    {
                        for theorem in base_cases {
                            claimed_proven.insert(theorem);
                        }
                        for theorem in induction_steps {
                            claimed_proven.insert(theorem);
                        }
                    }
                }
            }
            // Not the theorem itself, but its counterexample and, if the
            // report gives one, its `reachable` theorem
            TheoremStatus::Refuted {
                counterexample,
                reachable,
            } => {
                claimed_proven.insert(counterexample);
                if let Some(reachable) = reachable {
                    claimed_proven.insert(reachable);
                }
            }
            // No theorem
            TheoremStatus::Open { .. } | TheoremStatus::DroppedWrite { .. } => {}
        }
    }
    claimed_proven
}

/// Fails if `report` gives a status or a `dropped_writes` entry to a theorem
/// that is not in `spec_theorems`, or if a theorem in `claimed_proven` is not
/// in `spec_theorems`.
fn check_names_exist(
    report: &ProveReport,
    claimed_proven: &BTreeSet<&String>,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let mut unknown = BTreeSet::new();
    for name in report.theorems.keys().chain(report.dropped_writes.keys()) {
        if find_theorem(spec_theorems, name).is_none() {
            unknown.insert(name.clone());
        }
    }
    for name in claimed_proven {
        if find_theorem(spec_theorems, name).is_none() {
            unknown.insert((*name).clone());
        }
    }
    if !unknown.is_empty() {
        return Err(ProveGateFailure::UnknownTheorems {
            names: unknown.into_iter().collect(),
        });
    }
    Ok(())
}

/// Fails if a theorem in `claimed_proven` is not proven. Skips a name that is
/// not in `spec_theorems`.
fn check_claims_are_proven(
    claimed_proven: &BTreeSet<&String>,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let mut not_proven = Vec::new();
    for name in claimed_proven {
        let Some(theorem) = find_theorem(spec_theorems, name) else {
            continue;
        };
        if !is_proven(theorem) {
            let axioms = theorem.axioms.clone().unwrap_or_default();
            not_proven.push((theorem.name.clone(), axioms));
        }
    }
    if !not_proven.is_empty() {
        return Err(ProveGateFailure::NotProven {
            theorems: not_proven,
        });
    }
    Ok(())
}

/// Fails if a labelled theorem, or a theorem that is not proven, has no status
/// in `report`.
fn check_statuses_are_complete(
    report: &ProveReport,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let mut missing = Vec::new();
    for theorem in spec_theorems {
        let is_labelled = theorem.id.is_some();
        let needs_status = is_labelled || !is_proven(theorem);
        if needs_status && !report.theorems.contains_key(&theorem.name) {
            missing.push(theorem.name.clone());
        }
    }
    if !missing.is_empty() {
        return Err(ProveGateFailure::MissingStatus { theorems: missing });
    }
    Ok(())
}

/// Fails if a theorem in `claimed_proven` depends on
/// `Lusterna.refMutWriteDropped` and has no entry in the `dropped_writes` of
/// `report`. Skips a name that is not in `spec_theorems`.
fn check_dropped_writes_explained(
    report: &ProveReport,
    claimed_proven: &BTreeSet<&String>,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let mut unexplained = Vec::new();
    for name in claimed_proven {
        let Some(theorem) = find_theorem(spec_theorems, name) else {
            continue;
        };
        if theorem.drops_writes == Some(true) && !report.dropped_writes.contains_key(*name) {
            unexplained.push((*name).clone());
        }
    }
    if !unexplained.is_empty() {
        return Err(ProveGateFailure::UnexplainedDroppedWrites {
            theorems: unexplained,
        });
    }
    Ok(())
}

/// Returns, for each property that the formalize stage formalized, the
/// statuses in `report` of the theorems with its label.
fn group_by_property(
    input: &ProveInput,
    properties: &Properties,
    report: &ProveReport,
    spec_theorems: &[TheoremCheck],
) -> ProveOutput {
    let mut output = BTreeMap::new();
    for property in &properties.0 {
        // A property that the formalize stage skipped has no theorems
        let Some(PropertyFormalizationStatus::Formalized { .. }) =
            input.property_status.get(&property.id)
        else {
            continue;
        };
        let theorems = labelled_theorems(property.id, report, spec_theorems);
        let status = match &property.kind {
            PropertyKind::Rule { .. } => rule_status(theorems),
            PropertyKind::Invariant { r#struct } => {
                // The functions that create the struct, as the formalize stage
                // reported them
                let base_case_functions: &[String] = match input.induction.get(r#struct) {
                    Some(induction) => &induction.base_cases,
                    None => &[],
                };
                induction_status(theorems, base_case_functions)
            }
        };
        output.insert(property.id, status);
    }
    ProveOutput { properties: output }
}

/// Returns the theorems with the label of the property `id`, each with its
/// status in `report`. Leaves out a theorem without a status.
fn labelled_theorems<'a>(
    id: PropertyId,
    report: &ProveReport,
    spec_theorems: &'a [TheoremCheck],
) -> Vec<(&'a TheoremCheck, TheoremStatus)> {
    let mut theorems = Vec::new();
    for theorem in spec_theorems {
        if theorem.id != Some(id) {
            continue;
        }
        if let Some(status) = report.theorems.get(&theorem.name) {
            theorems.push((theorem, status.clone()));
        }
    }
    theorems
}

/// Returns the status of a rule property from the statuses of its theorems.
fn rule_status(theorems: Vec<(&TheoremCheck, TheoremStatus)>) -> PropertyProofStatus {
    let mut statuses = BTreeMap::new();
    for (theorem, status) in theorems {
        statuses.insert(theorem.name.clone(), status);
    }
    PropertyProofStatus::Rule { theorems: statuses }
}

/// Returns the status of an invariant property from the statuses of its
/// theorems. A theorem whose triple calls one of `base_case_functions` is a
/// base case, and every other theorem is an induction step. Each theorem is
/// listed under the function that its triple calls, or under its own name if
/// `Lusterna.Spec.check` reports no function for it.
fn induction_status(
    theorems: Vec<(&TheoremCheck, TheoremStatus)>,
    base_case_functions: &[String],
) -> PropertyProofStatus {
    let mut base_cases: BTreeMap<String, Vec<(String, TheoremStatus)>> = BTreeMap::new();
    let mut steps: BTreeMap<String, Vec<(String, TheoremStatus)>> = BTreeMap::new();
    for (theorem, status) in theorems {
        let function = match &theorem.function {
            Some(function) => function.clone(),
            None => theorem.name.clone(),
        };
        let group = if base_case_functions.contains(&function) {
            &mut base_cases
        } else {
            &mut steps
        };
        group
            .entry(function)
            .or_default()
            .push((theorem.name.clone(), status));
    }
    PropertyProofStatus::Induction { base_cases, steps }
}

/// Returns `text` as inline code in Markdown.
fn code(text: &str) -> String {
    format!("`{text}`")
}

/// Returns `items` as a Markdown list.
fn bullet_list(items: &[String]) -> String {
    let mut list = String::new();
    for item in items {
        if !list.is_empty() {
            list.push('\n');
        }
        list.push_str("- ");
        list.push_str(item);
    }
    list
}

/// Returns `names` as a Markdown list of inline code.
fn code_list(names: &[String]) -> String {
    let mut items = Vec::new();
    for name in names {
        items.push(code(name));
    }
    bullet_list(&items)
}

impl ProveGateFailure {
    /// Returns the message that tells the agent what failed and how to fix it.
    pub(super) fn to_feedback_prompt(&self) -> String {
        match self {
            ProveGateFailure::ChangedOutsideSpec { files } => {
                let mut paths = Vec::new();
                for file in files {
                    paths.push(file.display().to_string());
                }
                format!(
                    "These files differ from the last commit, but you may only change files \
                     under `Spec/`:\n\n{}\n\n\
                     Undo your changes to them: restore a changed file with \
                     `git restore --staged --worktree -- ':/<file>'`, and delete a file that you \
                     created.",
                    code_list(&paths)
                )
            }
            ProveGateFailure::BuildFailed { output } => format!(
                "`lake build Spec` failed with this output:\n\n```\n{output}\n```\n\n\
                 Fix the first error and run `lake build Spec` again until it passes.",
            ),
            ProveGateFailure::UnknownTheorems { names } => format!(
                "Your report names these theorems, but no theorem under `Spec/` has that \
                 name:\n\n{}\n\n\
                 Use the full Lean name of each theorem, and name only theorems that exist.",
                code_list(names)
            ),
            ProveGateFailure::NotProven { theorems } => {
                let mut items = Vec::new();
                for (name, axioms) in theorems {
                    let mut axioms_as_code = Vec::new();
                    for axiom in axioms {
                        axioms_as_code.push(code(axiom));
                    }
                    items.push(format!("{}: {}", code(name), axioms_as_code.join(", ")));
                }
                format!(
                    "Your report gives these theorems the status `Proven` or \
                     `ProvenWithAssumptions`, or names them in the status of another theorem, \
                     but they are not proven. Each depends on the axioms after its name:\n\n{}\n\n\
                     `sorryAx` means that the proof, or the proof of a lemma that it uses, is \
                     `sorry`. Any other axiom is declared in a file under `Spec/`, which \
                     `native_decide` also does. Finish the proof, or report the theorem as \
                     `Open`.",
                    bullet_list(&items)
                )
            }
            ProveGateFailure::MissingStatus { theorems } => format!(
                "These theorems have no status in your report:\n\n{}\n\n\
                 Give a status for every labelled theorem and for every theorem whose proof is \
                 not finished.",
                code_list(theorems)
            ),
            ProveGateFailure::UnexplainedDroppedWrites { theorems } => format!(
                "Your report claims that these theorems are proven, but each depends on the \
                 axiom `Lusterna.refMutWriteDropped`, and your report gives them no entry in \
                 `dropped_writes`:\n\n{}\n\n\
                 The proof of each goes through `deref_mut` of a `core.cell.RefMut`, and the \
                 translation drops every write through a `RefMut`. A theorem that holds only \
                 because such a write is lost says nothing about the Rust code. For each \
                 theorem, find the writes through a `RefMut` in the functions that its proof \
                 goes through. If its conclusion does not depend on them, add an entry to \
                 `dropped_writes` that names the writes and says why. Otherwise give the \
                 theorem, or the theorem whose counterexample it is, the status \
                 `DroppedWrite`.",
                code_list(theorems)
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::stage::{
        formalize::Induction,
        properties::Property,
        prove::{AdditionalAssumption, AssumptionPurpose},
    };

    /// A rule property with the id 1 and an invariant property with the id 2.
    fn properties() -> Properties {
        Properties(vec![
            Property {
                id: 1,
                kind: PropertyKind::Rule {
                    fns: vec!["module::f".to_owned()],
                },
                title: "f adds".to_owned(),
                natspec: String::new(),
            },
            Property {
                id: 2,
                kind: PropertyKind::Invariant {
                    r#struct: "module::Struct".to_owned(),
                },
                title: "Inv holds".to_owned(),
                natspec: String::new(),
            },
        ])
    }

    /// An input in which both properties are formalized, and `Struct::new`
    /// creates the struct of the invariant.
    fn input() -> ProveInput {
        ProveInput {
            extracted: PathBuf::new(),
            lean_root: PathBuf::new(),
            properties: PathBuf::new(),
            property_status: BTreeMap::from([
                (
                    1,
                    PropertyFormalizationStatus::Formalized {
                        file: PathBuf::from("Spec/FAdds.lean"),
                        theorems: vec!["Spec.FAdds.f_adds".to_owned()],
                    },
                ),
                (
                    2,
                    PropertyFormalizationStatus::Formalized {
                        file: PathBuf::from("Spec/Inv.lean"),
                        theorems: vec!["Spec.Inv.new".to_owned(), "Spec.Inv.f".to_owned()],
                    },
                ),
            ]),
            induction: BTreeMap::from([(
                "module::Struct".to_owned(),
                Induction {
                    base_cases: vec!["module.Struct.new".to_owned()],
                    steps: vec!["module.Struct.f".to_owned()],
                },
            )]),
        }
    }

    /// A theorem with the label `id` whose triple calls `function`.
    fn labelled(name: &str, id: PropertyId, function: &str, proven: bool) -> TheoremCheck {
        TheoremCheck {
            id: Some(id),
            function: Some(function.to_owned()),
            ..unlabelled(name, proven)
        }
    }

    /// A theorem without a label. If it is not `proven`, it depends on
    /// `sorryAx`.
    fn unlabelled(name: &str, proven: bool) -> TheoremCheck {
        let axioms = if proven {
            Vec::new()
        } else {
            vec!["sorryAx".to_owned()]
        };
        TheoremCheck {
            name: name.to_owned(),
            module: String::new(),
            id: None,
            title: None,
            function: None,
            issues: Vec::new(),
            axioms: Some(axioms),
            drops_writes: Some(false),
        }
    }

    /// The theorems under `Spec/`: the rule theorem and the base case are
    /// proven, the induction step is not, and its counterexample and a lemma
    /// are proven.
    fn spec_theorems() -> Vec<TheoremCheck> {
        vec![
            labelled("Spec.FAdds.f_adds", 1, "module.Struct.f", true),
            labelled("Spec.Inv.new", 2, "module.Struct.new", true),
            labelled("Spec.Inv.f", 2, "module.Struct.f", false),
            unlabelled("Spec.Inv.f_counterexample", true),
            unlabelled("Spec.InvAux.lemma", true),
        ]
    }

    /// A report that matches `spec_theorems`.
    fn report() -> ProveReport {
        ProveReport {
            theorems: BTreeMap::from([
                (
                    "Spec.FAdds.f_adds".to_owned(),
                    TheoremStatus::ProvenWithAssumptions {
                        assumptions: vec![AdditionalAssumption {
                            name: "h_ext".to_owned(),
                            reason: String::new(),
                            purpose: AssumptionPurpose::External {
                                justification: String::new(),
                            },
                        }],
                    },
                ),
                ("Spec.Inv.new".to_owned(), TheoremStatus::Proven),
                (
                    "Spec.Inv.f".to_owned(),
                    TheoremStatus::Refuted {
                        counterexample: "Spec.Inv.f_counterexample".to_owned(),
                        reachable: None,
                    },
                ),
            ]),
            dropped_writes: BTreeMap::new(),
        }
    }

    #[test]
    fn consistent_report_passes() {
        check_report(&report(), &spec_theorems()).unwrap();
    }

    #[test]
    fn open_theorem_passes() {
        let mut report = report();
        report.theorems.insert(
            "Spec.Inv.f".to_owned(),
            TheoremStatus::Open {
                reason: String::new(),
            },
        );
        check_report(&report, &spec_theorems()).unwrap();
    }

    #[test]
    fn rejects_status_of_unknown_theorem() {
        let mut report = report();
        report
            .theorems
            .insert("Spec.Inv.g".to_owned(), TheoremStatus::Proven);
        assert!(matches!(
            check_report(&report, &spec_theorems()),
            Err(ProveGateFailure::UnknownTheorems { names }) if names == ["Spec.Inv.g"]
        ));
    }

    #[test]
    fn rejects_unknown_reachable() {
        let mut report = report();
        report.theorems.insert(
            "Spec.Inv.f".to_owned(),
            TheoremStatus::Refuted {
                counterexample: "Spec.Inv.f_counterexample".to_owned(),
                reachable: Some("Spec.Inv.f_run".to_owned()),
            },
        );
        assert!(matches!(
            check_report(&report, &spec_theorems()),
            Err(ProveGateFailure::UnknownTheorems { names }) if names == ["Spec.Inv.f_run"]
        ));
    }

    #[test]
    fn rejects_proven_status_of_unproven_theorem() {
        let mut report = report();
        report
            .theorems
            .insert("Spec.Inv.f".to_owned(), TheoremStatus::Proven);
        assert!(matches!(
            check_report(&report, &spec_theorems()),
            Err(ProveGateFailure::NotProven { theorems })
                if theorems == [("Spec.Inv.f".to_owned(), vec!["sorryAx".to_owned()])]
        ));
    }

    #[test]
    fn rejects_unproven_counterexample() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[3] = unlabelled("Spec.Inv.f_counterexample", false);
        assert!(matches!(
            check_report(&report(), &spec_theorems),
            Err(ProveGateFailure::NotProven { theorems })
                if theorems == [("Spec.Inv.f_counterexample".to_owned(), vec!["sorryAx".to_owned()])]
        ));
    }

    #[test]
    fn rejects_unproven_induction_step_of_assumed_invariant() {
        let mut report = report();
        report.theorems.insert(
            "Spec.FAdds.f_adds".to_owned(),
            TheoremStatus::ProvenWithAssumptions {
                assumptions: vec![AdditionalAssumption {
                    name: "h_inv2".to_owned(),
                    reason: String::new(),
                    purpose: AssumptionPurpose::StateInvariant {
                        base_cases: vec!["Spec.Inv.new".to_owned()],
                        induction_steps: vec!["Spec.Inv.f".to_owned()],
                    },
                }],
            },
        );
        assert!(matches!(
            check_report(&report, &spec_theorems()),
            Err(ProveGateFailure::NotProven { theorems })
                if theorems == [("Spec.Inv.f".to_owned(), vec!["sorryAx".to_owned()])]
        ));
    }

    #[test]
    fn rejects_labelled_theorem_without_status() {
        let mut report = report();
        report.theorems.remove("Spec.Inv.new");
        assert!(matches!(
            check_report(&report, &spec_theorems()),
            Err(ProveGateFailure::MissingStatus { theorems }) if theorems == ["Spec.Inv.new"]
        ));
    }

    #[test]
    fn rejects_unproven_lemma_without_status() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[4] = unlabelled("Spec.InvAux.lemma", false);
        assert!(matches!(
            check_report(&report(), &spec_theorems),
            Err(ProveGateFailure::MissingStatus { theorems }) if theorems == ["Spec.InvAux.lemma"]
        ));
    }

    #[test]
    fn rejects_unexplained_dropped_write() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[3].drops_writes = Some(true);
        assert!(matches!(
            check_report(&report(), &spec_theorems),
            Err(ProveGateFailure::UnexplainedDroppedWrites { theorems })
                if theorems == ["Spec.Inv.f_counterexample"]
        ));
    }

    #[test]
    fn explained_dropped_write_passes() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[3].drops_writes = Some(true);
        let mut report = report();
        report
            .dropped_writes
            .insert("Spec.Inv.f_counterexample".to_owned(), String::new());
        check_report(&report, &spec_theorems).unwrap();
    }

    #[test]
    fn dropped_write_status_passes() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[3].drops_writes = Some(true);
        let mut report = report();
        report.theorems.insert(
            "Spec.Inv.f".to_owned(),
            TheoremStatus::DroppedWrite {
                function: String::new(),
                reason: String::new(),
            },
        );
        check_report(&report, &spec_theorems).unwrap();
    }

    #[test]
    fn groups_statuses_by_property() {
        let output = group_by_property(&input(), &properties(), &report(), &spec_theorems());
        assert_eq!(
            serde_json::to_value(&output).unwrap(),
            json!({"properties": {
                "1": {"Rule": {"theorems": {
                    "Spec.FAdds.f_adds": {"ProvenWithAssumptions": {"assumptions": [
                        {"name": "h_ext", "reason": "", "purpose": {"External": {"justification": ""}}}
                    ]}}
                }}},
                "2": {"Induction": {
                    "base_cases": {"module.Struct.new": [["Spec.Inv.new", "Proven"]]},
                    "steps": {"module.Struct.f": [["Spec.Inv.f", {"Refuted": {
                        "counterexample": "Spec.Inv.f_counterexample",
                        "reachable": null
                    }}]]}
                }}
            }})
        );
    }

    #[test]
    fn feedback_lists_theorems_and_axioms() {
        let failure = ProveGateFailure::NotProven {
            theorems: vec![("Spec.Inv.f".to_owned(), vec!["sorryAx".to_owned()])],
        };
        assert!(
            failure
                .to_feedback_prompt()
                .contains("- `Spec.Inv.f`: `sorryAx`")
        );
    }
}
