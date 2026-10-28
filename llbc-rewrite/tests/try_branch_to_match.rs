use std::{fs, path::Path, process::Command};

use charon_lib::{
    ast::TranslatedCrate,
    export::{CharonVersion, CrateData},
    options::SerializationFormat,
};

const FIXTURES: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/fixtures/try_branch_to_match"
);

/// Builds a crate from the fixture `name`, runs Charon on it and returns the path of the LLBC
/// file.
fn charon(dir: &Path, name: &str) -> std::path::PathBuf {
    fs::create_dir_all(dir.join("src")).unwrap();
    fs::write(
        dir.join("Cargo.toml"),
        "[package]\nname = \"fixture\"\nversion = \"0.1.0\"\nedition = \"2021\"\n\n[workspace]\n",
    )
    .unwrap();
    fs::copy(
        Path::new(FIXTURES).join(format!("{name}.rs")),
        dir.join("src/lib.rs"),
    )
    .unwrap();
    let llbc = dir.join("fixture.llbc");
    let out = Command::new("charon")
        .args(["cargo", "--preset=aeneas", "--dest-file"])
        .arg(&llbc)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(out.status.success(), "{out:?}");
    llbc
}

/// Asserts that Aeneas translates the crate without an error, and that the Lean code does not
/// call `branch`.
fn assert_rewritten(dir: &Path, translated: TranslatedCrate) {
    let llbc = dir.join("rewritten.llbc");
    CrateData {
        charon_version: CharonVersion(charon_lib::VERSION.to_owned()),
        translated,
        has_errors: false,
    }
    .serialize_to_file(&llbc, SerializationFormat::Json)
    .unwrap();
    let out = Command::new("aeneas")
        .args(["-backend", "lean", "-dest"])
        .arg(dir.join("lean"))
        .arg(&llbc)
        .output()
        .unwrap();
    let output = String::from_utf8_lossy(&out.stdout) + String::from_utf8_lossy(&out.stderr);
    assert!(
        out.status.success() && !output.contains("Error"),
        "{output}"
    );
    let lean = fs::read_to_string(dir.join("lean/Rewritten.lean")).unwrap();
    assert!(!lean.contains("CoreOpsTry.branch"), "{lean}");
}

fn rewrite(name: &str) -> (tempfile::TempDir, Option<TranslatedCrate>) {
    let dir = tempfile::tempdir().unwrap();
    let llbc = charon(dir.path(), name);
    let result = llbc_rewrite::run(&llbc)
        .unwrap()
        .map(|crate_data| crate_data.translated);
    (dir, result)
}

#[test]
fn rewrites_result() {
    let (dir, result) = rewrite("rewrite_result");
    assert_rewritten(dir.path(), result.unwrap());
}

#[test]
fn rewrites_refmut() {
    let (dir, result) = rewrite("rewrite_refmut");
    assert_rewritten(dir.path(), result.unwrap());
}

#[test]
fn skips_control_flow_used() {
    assert!(rewrite("skip_control_flow_used").1.is_none());
}
