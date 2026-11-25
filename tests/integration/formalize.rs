use std::fs;

use lusterna::stage::formalize::{FormalizeSubmission, PropertyFormalizationStatus, SkipReason};

use super::{
    CHECKED_TOTAL, LOG_BUCKET_NOT_ZERO, MUL_DIV, UNREACHABLE, VAULT_SHARES, commit_with_subject,
    formalized, git, lean_root, submission, theorem_block,
};

/// The proof of the theorem in `block`, after the `:=` of its statement
fn proof(block: &str) -> &str {
    let start = block.find("-- Statement").expect("no `-- Statement`");
    let end = start + block[start..].find(":=").expect("no `:=`");
    block[end + 2..].trim()
}

/// The induction of `Vault` has `new` as its base case, the public mutators as
/// its steps and `set` as its helper.
#[test]
fn induction_of_vault_lists_its_operations() {
    let formalize: FormalizeSubmission = submission("formalize");
    let induction = &formalize.induction["vault::Vault"];
    assert_eq!(induction.base_cases, ["demo.vault.Vault.new"]);
    let mut steps = induction.steps.clone();
    steps.sort();
    assert_eq!(
        steps,
        [
            "demo.vault.Vault.deposit",
            "demo.vault.Vault.reset",
            "demo.vault.Vault.withdraw"
        ]
    );
    let mut callers = induction.helpers["demo.vault.Vault.set"].clone();
    callers.sort();
    assert_eq!(callers, ["demo.vault.Vault.deposit", "demo.vault.Vault.withdraw"]);
    assert_eq!(induction.helpers.len(), 1);
    assert!(induction.unmodelled_steps.is_empty());
}

/// Only the property about `double`, which no entrypoint reaches, is skipped,
/// as `MissingTranslation`.
#[test]
fn unreachable_function_is_skipped() {
    let formalize: FormalizeSubmission = submission("formalize");
    for (id, status) in &formalize.property_status {
        match status {
            PropertyFormalizationStatus::Skipped(SkipReason::MissingTranslation { items }) => {
                assert_eq!(*id, UNREACHABLE);
                assert!(items.iter().any(|i| i.contains("double")), "{items:?}");
            }
            PropertyFormalizationStatus::Skipped(reason) => {
                panic!("property {id} is skipped: {reason:?}")
            }
            PropertyFormalizationStatus::Formalized { .. } => assert_ne!(*id, UNREACHABLE),
        }
    }
}

/// Every theorem has the proof `by sorry` in the formalize commit.
#[test]
fn formalize_leaves_every_theorem_unproven() {
    let formalize: FormalizeSubmission = submission("formalize");
    let commit = commit_with_subject("Lusterna: `formalize` stage");
    for id in [CHECKED_TOTAL, LOG_BUCKET_NOT_ZERO, VAULT_SHARES, MUL_DIV] {
        let (file, theorems) = formalized(&formalize, id);
        let text = git(&["show", &format!("{commit}:lusterna/lean/{}", file.display())]);
        for name in theorems {
            let block = theorem_block(&text, &file, &name);
            let proof: Vec<&str> = proof(&block).split_whitespace().collect();
            assert_eq!(proof, ["by", "sorry"], "{name}");
        }
    }
}

/// The file of every formalized property has a module doc comment.
#[test]
fn spec_files_have_a_module_doc() {
    let formalize: FormalizeSubmission = submission("formalize");
    for id in [CHECKED_TOTAL, LOG_BUCKET_NOT_ZERO, VAULT_SHARES, MUL_DIV] {
        let (file, _) = formalized(&formalize, id);
        let text = fs::read_to_string(lean_root().join(&file)).unwrap();
        assert!(text.contains("/-!"), "{}", file.display());
    }
}
