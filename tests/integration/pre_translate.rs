use std::fs;

use lusterna::stage::pre_translate::PreTranslateSubmission;

use super::{run_dir, submission};

/// The size check that fails on the host is kept, under `#[cfg(target_os = "solana")]`.
#[test]
fn size_check_is_gated_to_solana() {
    let lib = fs::read_to_string(run_dir().join("src/lib.rs")).unwrap();
    let lines: Vec<&str> = lib.lines().map(str::trim).collect();
    let check = lines
        .iter()
        .position(|l| l.contains("align_of::<u128>() == 8"))
        .expect("the size check is removed");
    let attribute = check.checked_sub(1).map(|i| lines[i]);
    assert_eq!(attribute, Some("#[cfg(target_os = \"solana\")]"));
}

/// The submission lists the change to `src/lib.rs`.
#[test]
fn pre_translate_reports_its_change() {
    let pre_translate: PreTranslateSubmission = submission("pretranslate");
    assert!(pre_translate.changes.iter().any(|c| c.file == "src/lib.rs"));
}
