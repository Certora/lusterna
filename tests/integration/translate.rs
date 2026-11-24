use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::Command,
};

use lusterna::stage::translate::TranslateSubmission;

use super::{fixture, lean_root, run_dir, submission};

/// Defines `okEq` and `failsWith`, which compare a `Result` with a value or an
/// error. `Result` has no `DecidableEq`.
const LEAN_HELPERS: &str = "\
open Aeneas Aeneas.Std Result

def okEq {α : Type} [DecidableEq α] (r : Result α) (v : α) : Bool :=
  match r.match with
  | .ok x => decide (x = v)
  | _ => false

def failsWith {α : Type} (r : Result α) (e : Error) : Bool :=
  match r.match, e with
  | .vis (.fail .panic) _, .panic => true
  | .vis (.fail .integerOverflow) _, .integerOverflow => true
  | _, _ => false
";

/// The `.rs` files under `dir`
fn rust_files(dir: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let Ok(entries) = fs::read_dir(dir) else {
        return files;
    };
    for entry in entries {
        let path = entry.unwrap().path();
        if path.is_dir() {
            files.extend(rust_files(&path));
        } else if path.extension().is_some_and(|e| e == "rs") {
            files.push(path);
        }
    }
    files
}

/// Checks `source` with `lake env lean` in the Lean project of the run, after
/// `import Extraction.Funs` and `LEAN_HELPERS`.
fn lean_check(source: &str) {
    let mut file = tempfile::Builder::new().suffix(".lean").tempfile().unwrap();
    write!(file, "import Extraction.Funs\n{LEAN_HELPERS}\n{source}").unwrap();
    let output = Command::new("lake")
        .args(["env", "lean"])
        .arg(file.path())
        .current_dir(lean_root())
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

/// No Rust item is marked `charon::opaque`, and `extract-args.sh` adds no
/// `--exclude` or `--opaque`.
#[test]
fn no_code_is_hidden_from_charon() {
    for file in rust_files(&run_dir().join("src")) {
        let text = fs::read_to_string(&file).unwrap();
        assert!(!text.contains("charon::opaque"), "{}", file.display());
    }
    let args = fs::read_to_string(run_dir().join("lusterna/extract-args.sh")).unwrap();
    for line in args.lines().filter(|l| !l.trim_start().starts_with('#')) {
        assert!(!line.contains("--exclude"), "{line}");
        assert!(!line.contains("--opaque"), "{line}");
    }
}

/// `Funs.lean` defines each function of the entrypoints, and `find_pair` is not
/// an axiom.
#[test]
fn every_entrypoint_is_translated() {
    let extraction = lean_root().join("Extraction");
    let funs = fs::read_to_string(extraction.join("Funs.lean")).unwrap();
    for name in [
        "math.checked_total",
        "math.log_bucket",
        "pairs.find_pair",
        "vault.Vault.new",
        "vault.Vault.deposit",
        "vault.Vault.withdraw",
        "vault.Vault.reset",
        "wide.mul_div",
    ] {
        assert!(funs.contains(&format!("def {name}")), "no `def {name}`");
    }
    let external = fs::read_to_string(extraction.join("FunsExternal.lean")).unwrap();
    assert!(!external.contains("axiom pairs."));
}

/// The source files other than `lib.rs` and `pairs.rs` are the same as in the
/// test crate.
#[test]
fn code_outside_the_failing_function_is_unchanged() {
    for file in ["math.rs", "unused.rs", "vault.rs", "wide.rs"] {
        let original = fs::read_to_string(fixture().join("src").join(file)).unwrap();
        let run = fs::read_to_string(run_dir().join("src").join(file)).unwrap();
        assert_eq!(run, original, "src/{file} changed");
    }
}

/// No file under `src/lusterna/` mentions `U256`, the type from the stubbed
/// crate `uint`.
#[test]
fn stubbed_type_is_not_replaced() {
    for file in rust_files(&run_dir().join("src/lusterna")) {
        let text = fs::read_to_string(&file).unwrap();
        assert!(!text.contains("U256"), "{}", file.display());
    }
}

/// The external modules import `Extraction.Lusterna` and neither declare nor
/// define a `uint` item.
#[test]
fn external_modules_use_the_stub_models() {
    let extraction = lean_root().join("Extraction");
    for module in ["FunsExternal.lean", "TypesExternal.lean"] {
        let Ok(text) = fs::read_to_string(extraction.join(module)) else {
            continue;
        };
        assert!(text.contains("import Extraction.Lusterna"), "{module}");
        for line in text.lines() {
            assert!(!line.starts_with("axiom uint."), "{module}: {line}");
            assert!(!line.starts_with("def uint."), "{module}: {line}");
        }
    }
}

/// The submission lists a change to `src/pairs.rs`, and `src/lusterna/` has
/// Rust files.
#[test]
fn nested_loop_is_replaced_under_src_lusterna() {
    let translate: TranslateSubmission = submission("translate");
    assert!(translate.rust_changes.iter().any(|c| c.file == "src/pairs.rs"));
    assert!(!rust_files(&run_dir().join("src/lusterna")).is_empty());
}

/// The model of `u64::ilog2` returns the results of Rust and fails on 0.
#[test]
fn ilog2_model_matches_rust() {
    lean_check(
        "#guard failsWith (core.num.U64.ilog2 0#u64) .panic\n\
         #guard okEq (core.num.U64.ilog2 1#u64) 0#u32\n\
         #guard okEq (core.num.U64.ilog2 8#u64) 3#u32\n\
         #guard okEq (core.num.U64.ilog2 0xFFFFFFFFFFFFFFFF#u64) 63#u32\n",
    );
}

/// The translation of `mul_div` returns the results of Rust and fails where Rust
/// panics.
#[test]
fn mul_div_computes_through_the_stub_models() {
    lean_check(
        "#guard okEq (demo.wide.mul_div 6#u64 7#u64 4#u64) 10#u64\n\
         #guard failsWith (demo.wide.mul_div 1#u64 1#u64 0#u64) .panic\n\
         #guard failsWith (demo.wide.mul_div 0xFFFFFFFFFFFFFFFF#u64 0xFFFFFFFFFFFFFFFF#u64 1#u64) .panic\n",
    );
}
