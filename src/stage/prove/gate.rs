//! The gate of the prove stage. It checks the work of the agent in this order:
//!
//! 1. Only files under `Spec/` differ from `HEAD`.
//! 2. `lake build Spec` passes.
//! 3. Every theorem that the submission names is a theorem under `Spec/`.
//! 4. Every theorem that the submission claims to be proven is proven.
//! 5. Every labelled theorem and every theorem that is not proven has a status
//!    in the submission.
//! 6. Every theorem that the submission claims to be proven and that depends on
//!    `Lusterna.refMutWriteDropped` has an entry in its `dropped_writes`.
//!
//! Then it groups the statuses of the submission by property.
//!
//! Labelled theorem: a theorem tagged `@[lusterna id "title"]`, which states
//! the property with that `id`.
//!
//! A theorem is proven if `Lusterna.Spec.check` reports no axioms for it.
//!
//! The submission claims that a theorem is proven if it
//! - gives the theorem the status `Proven` or `ProvenWithAssumptions`, or
//! - names it in the `base_cases` or the `induction_steps` of a
//!   `StateInvariant` assumption.

use std::{
    collections::BTreeSet,
    io,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    stage::prove::{AssumptionPurpose, ProveInput, ProveSubmission, TheoremStatus},
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
    #[display("the submission names {} theorems that do not exist", names.len())]
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

/// Runs the gate checks on the work of the agent in the Lean project at
/// `input.lean_root`, and returns the first check that fails.
pub(super) fn run_gate(
    input: &ProveInput,
    submission: &ProveSubmission,
) -> Result<Option<ProveGateFailure>, ProveGateError> {
    if let Some(failure) = check_only_spec_changed(&input.lean_root)? {
        return Ok(Some(failure));
    }
    if let Some(failure) = check_spec_builds(&input.lean_root)? {
        return Ok(Some(failure));
    }

    // What Lean reports about each theorem under `Spec/`
    let spec_theorems = spec_check::run(&input.lean_root, true)?;
    Ok(check_submission(submission, &spec_theorems).err())
}

/// Fails if a file outside `Spec/` differs from `HEAD`.
fn check_only_spec_changed(lean_root: &Path) -> Result<Option<ProveGateFailure>, ProveGateError> {
    let spec = lean_root.join("Spec").canonicalize()?;
    let files = git::uncommitted_files_outside(lean_root, &spec)?;
    if !files.is_empty() {
        return Ok(Some(ProveGateFailure::ChangedOutsideSpec { files }));
    }
    Ok(None)
}

/// Fails if `lake build Spec` fails.
fn check_spec_builds(lean_root: &Path) -> Result<Option<ProveGateFailure>, ProveGateError> {
    match lean::lake_build(lean_root, &["Spec"]) {
        Ok(()) => Ok(None),
        // The build ran and failed. The agent can fix this.
        Err(LakeError::Build { output, .. }) => Ok(Some(ProveGateFailure::BuildFailed { output })),
        // `lake` could not run. The agent cannot fix this.
        Err(error) => Err(error.into()),
    }
}

/// Checks `submission` against `spec_theorems`, the theorems under `Spec/`, and
/// returns the first check that fails.
fn check_submission(
    submission: &ProveSubmission,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let claimed_proven = theorems_claimed_proven(submission);
    check_names_exist(submission, &claimed_proven, spec_theorems)?;
    check_claims_are_proven(&claimed_proven, spec_theorems)?;
    check_statuses_are_complete(submission, spec_theorems)?;
    check_dropped_writes_explained(submission, &claimed_proven, spec_theorems)
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

/// Returns the full Lean names of the theorems that `submission` claims to be
/// proven.
fn theorems_claimed_proven(submission: &ProveSubmission) -> BTreeSet<&String> {
    let mut claimed_proven = BTreeSet::new();
    for (name, status) in &submission.theorems {
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
            // No theorem
            TheoremStatus::Refuted { .. }
            | TheoremStatus::Open { .. }
            | TheoremStatus::Exception { .. }
            | TheoremStatus::DroppedWrite { .. } => {}
        }
    }
    claimed_proven
}

/// Fails if `submission` gives a status or a `dropped_writes` entry to a
/// theorem that is not in `spec_theorems`, or if a theorem in `claimed_proven`
/// is not in `spec_theorems`.
fn check_names_exist(
    submission: &ProveSubmission,
    claimed_proven: &BTreeSet<&String>,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let mut unknown = BTreeSet::new();
    for name in submission
        .theorems
        .keys()
        .chain(submission.dropped_writes.keys())
    {
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
/// in `submission`.
fn check_statuses_are_complete(
    submission: &ProveSubmission,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let mut missing = Vec::new();
    for theorem in spec_theorems {
        let is_labelled = theorem.id.is_some();
        let needs_status = is_labelled || !is_proven(theorem);
        if needs_status && !submission.theorems.contains_key(&theorem.name) {
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
/// `submission`. Skips a name that is not in `spec_theorems`.
fn check_dropped_writes_explained(
    submission: &ProveSubmission,
    claimed_proven: &BTreeSet<&String>,
    spec_theorems: &[TheoremCheck],
) -> Result<(), ProveGateFailure> {
    let mut unexplained = Vec::new();
    for name in claimed_proven {
        let Some(theorem) = find_theorem(spec_theorems, name) else {
            continue;
        };
        if theorem.drops_writes == Some(true) && !submission.dropped_writes.contains_key(*name) {
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
                "Your submission names these theorems, but no theorem under `Spec/` has that \
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
                    "Your submission gives these theorems the status `Proven` or \
                     `ProvenWithAssumptions`, or names them in the status of another theorem, \
                     but they are not proven. Each depends on the axioms after its name:\n\n{}\n\n\
                     `sorryAx` means that the proof, or the proof of a lemma that it uses, is \
                     `sorry`. Any other axiom is declared in a file under `Spec/`, which \
                     `native_decide` also does. Finish the proof, or submission the theorem as \
                     `Open`.",
                    bullet_list(&items)
                )
            }
            ProveGateFailure::MissingStatus { theorems } => format!(
                "These theorems have no status in your submission:\n\n{}\n\n\
                 Give a status for every labelled theorem and for every theorem whose proof is \
                 not finished.",
                code_list(theorems)
            ),
            ProveGateFailure::UnexplainedDroppedWrites { theorems } => format!(
                "Your submission claims that these theorems are proven, but each depends on the \
                 axiom `Lusterna.refMutWriteDropped`, and your submission gives them no entry in \
                 `dropped_writes`:\n\n{}\n\n\
                 The proof of each goes through `deref_mut` of a `core.cell.RefMut`, and the \
                 translation drops every write through a `RefMut`. A theorem that holds only \
                 because such a write is lost says nothing about the Rust code. For each \
                 theorem, find the writes through a `RefMut` in the functions that its proof \
                 goes through. If its conclusion does not depend on them, add an entry to \
                 `dropped_writes` that names the writes and says why. Otherwise give the \
                 theorem the status `DroppedWrite`. If the status of another theorem names \
                 it, give that theorem this status instead.",
                code_list(theorems)
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stage::prove::{
        AdditionalAssumption, AssumptionPurpose,
        utils::tests::{spec_theorems, submission, unlabelled},
    };

    #[test]
    fn consistent_submission_passes() {
        check_submission(&submission(), &spec_theorems()).unwrap();
    }

    #[test]
    fn open_theorem_passes() {
        let mut submission = submission();
        submission.theorems.insert(
            "Spec.Inv.f".to_owned(),
            TheoremStatus::Open {
                reason: String::new(),
            },
        );
        check_submission(&submission, &spec_theorems()).unwrap();
    }

    #[test]
    fn rejects_status_of_unknown_theorem() {
        let mut submission = submission();
        submission
            .theorems
            .insert("Spec.Inv.g".to_owned(), TheoremStatus::Proven);
        assert!(matches!(
            check_submission(&submission, &spec_theorems()),
            Err(ProveGateFailure::UnknownTheorems { names }) if names == ["Spec.Inv.g"]
        ));
    }

    #[test]
    fn rejects_proven_status_of_unproven_theorem() {
        let mut submission = submission();
        submission
            .theorems
            .insert("Spec.Inv.f".to_owned(), TheoremStatus::Proven);
        assert!(matches!(
            check_submission(&submission, &spec_theorems()),
            Err(ProveGateFailure::NotProven { theorems })
                if theorems == [("Spec.Inv.f".to_owned(), vec!["sorryAx".to_owned()])]
        ));
    }

    #[test]
    fn rejects_unproven_induction_step_of_assumed_invariant() {
        let mut submission = submission();
        submission.theorems.insert(
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
            check_submission(&submission, &spec_theorems()),
            Err(ProveGateFailure::NotProven { theorems })
                if theorems == [("Spec.Inv.f".to_owned(), vec!["sorryAx".to_owned()])]
        ));
    }

    #[test]
    fn rejects_labelled_theorem_without_status() {
        let mut submission = submission();
        submission.theorems.remove("Spec.Inv.new");
        assert!(matches!(
            check_submission(&submission, &spec_theorems()),
            Err(ProveGateFailure::MissingStatus { theorems }) if theorems == ["Spec.Inv.new"]
        ));
    }

    #[test]
    fn rejects_unproven_lemma_without_status() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[3] = unlabelled("Spec.InvAux.lemma", false);
        assert!(matches!(
            check_submission(&submission(), &spec_theorems),
            Err(ProveGateFailure::MissingStatus { theorems }) if theorems == ["Spec.InvAux.lemma"]
        ));
    }

    #[test]
    fn rejects_unexplained_dropped_write() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[1].drops_writes = Some(true);
        assert!(matches!(
            check_submission(&submission(), &spec_theorems),
            Err(ProveGateFailure::UnexplainedDroppedWrites { theorems })
                if theorems == ["Spec.Inv.new"]
        ));
    }

    #[test]
    fn explained_dropped_write_passes() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[1].drops_writes = Some(true);
        let mut submission = submission();
        submission
            .dropped_writes
            .insert("Spec.Inv.new".to_owned(), String::new());
        check_submission(&submission, &spec_theorems).unwrap();
    }

    #[test]
    fn dropped_write_status_passes() {
        let mut spec_theorems = spec_theorems();
        spec_theorems[1].drops_writes = Some(true);
        let mut submission = submission();
        submission.theorems.insert(
            "Spec.Inv.new".to_owned(),
            TheoremStatus::DroppedWrite {
                function: String::new(),
                reason: String::new(),
            },
        );
        check_submission(&submission, &spec_theorems).unwrap();
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
