use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

use lusterna::stage::{
    Stage,
    translate::{TranslateInput, TranslateStage},
};

fn copy_dir(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_dir(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

#[test]
fn setup_bootstraps_extraction_of_workspace_member() {
    let aeneas_root = PathBuf::from(
        env::var("AENEAS_ROOT").expect("AENEAS_ROOT must point to an Aeneas checkout"),
    );
    let rev = Command::new("git")
        .arg("-C")
        .arg(&aeneas_root)
        .args(["rev-parse", "HEAD"])
        .output()
        .unwrap();
    assert!(rev.status.success(), "{rev:?}");
    let aeneas_rev = String::from_utf8(rev.stdout).unwrap().trim().to_owned();
    let lean_toolchain =
        fs::read_to_string(aeneas_root.join("backends/lean/lean-toolchain")).unwrap();

    let dir = tempfile::tempdir().unwrap();
    let workspace_root = dir.path().join("workspace");
    copy_dir(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/translate_workspace"),
        &workspace_root,
    );
    let crate_root = workspace_root.join("core");
    eprintln!("copied fixture to {}", workspace_root.display());
    let git = Command::new("git")
        .arg("-C")
        .arg(&workspace_root)
        .args(["init", "-q"])
        .output()
        .unwrap();
    assert!(git.status.success(), "{git:?}");

    let mut stage = TranslateStage::new(TranslateInput {
        workspace_root: workspace_root.clone(),
        crate_root: Some(crate_root.clone()),
        entrypoints: vec!["crate::clamp".to_owned()],
        opaque: vec!["crate::min".to_owned()],
        aeneas_rev,
        lean_toolchain,
    });
    eprintln!("running setup (cargo build, lake update)");
    stage.setup().unwrap();

    let manifest = fs::read_to_string(crate_root.join("Cargo.toml")).unwrap();
    assert!(manifest.contains("lusterna = []"), "{manifest}");
    let lean_root = crate_root.join("lusterna/lean");
    assert!(lean_root.join("lakefile.toml").exists());
    assert!(lean_root.join("lean-toolchain").exists());

    eprintln!("running extract.sh (charon, aeneas)");
    let extract = Command::new(crate_root.join("lusterna/extract.sh"))
        .output()
        .unwrap();
    assert!(extract.status.success(), "{extract:?}");
    let extraction = fs::read_to_string(lean_root.join("Extraction.lean")).unwrap();
    assert!(extraction.contains("clamp"), "{extraction}");

    eprintln!("running lake build");
    let build = Command::new("lake")
        .arg("build")
        .current_dir(&lean_root)
        .output()
        .unwrap();
    assert!(build.status.success(), "{build:?}");
}
