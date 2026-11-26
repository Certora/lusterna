use std::fs;

use lusterna::stage::{
    formalize::{FormalizeSubmission, PropertyFormalizationStatus},
    properties::PropertyId,
    prove::{AssumptionPurpose, ProveSubmission, TheoremStatus},
};

use super::{
    CHECKED_TOTAL, LOG_BUCKET_NOT_ZERO, MUL_DIV, VAULT_SHARES, commit_with_subject, formalized,
    git, lean_root, submission, theorem_block,
};

/// The statement of the theorem in `block`, from `-- Statement` to `:=`
fn statement(block: &str) -> &str {
    let start = block.find("-- Statement").expect("no `-- Statement`");
    let end = start + block[start..].find(":=").expect("no `:=`");
    &block[start..end]
}

/// The theorems of the formalized property `id` whose statement calls a
/// function that its `exceptions` name
fn exception_theorems(formalize: &FormalizeSubmission, id: PropertyId) -> Vec<String> {
    let PropertyFormalizationStatus::Formalized {
        file,
        theorems,
        exceptions,
    } = &formalize.property_status[&id]
    else {
        panic!("property {id} is skipped");
    };
    let text = fs::read_to_string(lean_root().join(file)).unwrap();
    let mut names = Vec::new();
    for name in theorems {
        let block = theorem_block(&text, file, name);
        if exceptions
            .keys()
            .any(|f| statement(&block).contains(f.as_str()))
        {
            names.push(name.clone());
        }
    }
    names
}

/// Whether `status` is `Proven`, or `ProvenWithAssumptions` with only
/// assumptions of invariants
fn proven(status: &TheoremStatus) -> bool {
    match status {
        TheoremStatus::Proven => true,
        TheoremStatus::ProvenWithAssumptions { assumptions } => assumptions
            .iter()
            .all(|a| matches!(a.purpose, AssumptionPurpose::StateInvariant(_))),
        _ => false,
    }
}

/// `reset` is the only exception of the `Vault` invariant, and the theorems that
/// call it have the status `Exception`.
#[test]
fn reset_is_the_exception_of_the_invariant() {
    let formalize: FormalizeSubmission = submission("formalize");
    let PropertyFormalizationStatus::Formalized { exceptions, .. } =
        &formalize.property_status[&VAULT_SHARES]
    else {
        panic!("property {VAULT_SHARES} is skipped");
    };
    let functions: Vec<&String> = exceptions.keys().collect();
    assert_eq!(functions, ["demo.vault.Vault.reset"]);

    let prove: ProveSubmission = submission("prove");
    let names = exception_theorems(&formalize, VAULT_SHARES);
    assert!(!names.is_empty());
    for name in names {
        assert!(
            matches!(prove.theorems[&name], TheoremStatus::Exception { .. }),
            "{name}: {:?}",
            prove.theorems[&name]
        );
    }
}

/// Every theorem of the false property about `log_bucket` is `Refuted`.
#[test]
fn false_property_is_refuted() {
    let formalize: FormalizeSubmission = submission("formalize");
    let prove: ProveSubmission = submission("prove");
    let (_, theorems) = formalized(&formalize, LOG_BUCKET_NOT_ZERO);
    assert!(!theorems.is_empty());
    for name in theorems {
        assert!(
            matches!(prove.theorems[&name], TheoremStatus::Refuted { .. }),
            "{name}: {:?}",
            prove.theorems[&name]
        );
    }
}

/// Every theorem of the true properties, except those that call an exception,
/// is proven, with no assumption other than invariants.
#[test]
fn true_properties_are_proven() {
    let formalize: FormalizeSubmission = submission("formalize");
    let prove: ProveSubmission = submission("prove");
    for id in [CHECKED_TOTAL, VAULT_SHARES, MUL_DIV] {
        let (_, theorems) = formalized(&formalize, id);
        let exceptions = exception_theorems(&formalize, id);
        for name in theorems.iter().filter(|t| !exceptions.contains(*t)) {
            assert!(
                proven(&prove.theorems[name]),
                "{name}: {:?}",
                prove.theorems[name]
            );
        }
    }
}

/// The statement of every theorem is the same as in the formalize commit.
#[test]
fn prove_keeps_every_statement() {
    let formalize: FormalizeSubmission = submission("formalize");
    let commit = commit_with_subject("Lusterna: `formalize` stage");
    for id in [CHECKED_TOTAL, LOG_BUCKET_NOT_ZERO, VAULT_SHARES, MUL_DIV] {
        let (file, theorems) = formalized(&formalize, id);
        let before = git(&[
            "show",
            &format!("{commit}:lusterna/lean/{}", file.display()),
        ]);
        let after = fs::read_to_string(lean_root().join(&file)).unwrap();
        for name in theorems {
            assert_eq!(
                statement(&theorem_block(&after, &file, &name)),
                statement(&theorem_block(&before, &file, &name)),
                "{name}"
            );
        }
    }
}
