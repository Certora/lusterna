use std::{fs, path::Path, process::Command};

use charon_lib::{
    ast::{FunSource, TranslatedCrate, TyKind},
    export::{CharonVersion, CrateData},
    options::SerializationFormat,
};

const FIXTURES: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/fixtures/dyn_param_to_type_param"
);

/// Builds a crate from the fixture `name` and `loader.rs`, runs Charon on it and returns the path
/// of the LLBC file.
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
    fs::copy(
        Path::new(FIXTURES).join("loader.rs"),
        dir.join("src/loader.rs"),
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

/// Asserts that no function outside trait impls and vtables has a `dyn` parameter, and that
/// Aeneas translates the crate without an error.
fn assert_rewritten(dir: &Path, translated: TranslatedCrate) {
    for f in translated.fun_decls.iter() {
        if !matches!(f.src, FunSource::Normal) {
            continue;
        }
        for ty in f.signature.inputs.iter() {
            if let TyKind::Ref(_, inner, _) = ty.kind() {
                assert!(!inner.kind().is_dyn_trait(), "{:?}", f.item_meta.name);
            }
        }
    }
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
    assert!(out.status.success() && !output.contains("Error"), "{output}");
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
fn rewrites_call_chain() {
    let (dir, result) = rewrite("rewrite_call_chain");
    assert_rewritten(dir.path(), result.unwrap());
}

#[test]
fn rewrites_entrypoint() {
    let (dir, result) = rewrite("rewrite_entrypoint");
    assert_rewritten(dir.path(), result.unwrap());
}

#[test]
fn skips_trait_method() {
    assert!(rewrite("skip_trait_method").1.is_none());
}

#[test]
fn skips_runtime_choice() {
    assert!(rewrite("skip_runtime_choice").1.is_none());
}

#[test]
fn skips_struct_field() {
    assert!(rewrite("skip_struct_field").1.is_none());
}

#[test]
fn skips_stored() {
    assert!(rewrite("skip_stored").1.is_none());
}

#[test]
fn skips_fn_pointer() {
    assert!(rewrite("skip_fn_pointer").1.is_none());
}
