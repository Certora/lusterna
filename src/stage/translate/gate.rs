use std::{
    fs, io,
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

use crate::{
    stage::translate::{
        TranslateInput,
        setup::{self, SetupOutcome},
    },
    utils::{
        cargo::{self, CargoError},
        lean::{self, LakeError},
    },
};

/// How long `cargo test lusterna_test` may run in the gate check.
const EQUIV_TEST_TIMEOUT: Duration = Duration::from_secs(10 * 60);

#[derive(Debug, thiserror::Error)]
pub enum TranslateGateErr {
    #[error("cannot read `{script}`: {source}")]
    ReadScript {
        script: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("`{script}` must contain this line unchanged: {line}")]
    MissingLine { script: PathBuf, line: String },
    #[error(
        "`{script}` must not set ENTRYPOINTS or OPAQUE or pass --start-from or --opaque here: {line}"
    )]
    UnexpectedLine { script: PathBuf, line: String },
    #[error("the `charon` command in `{script}` must pass \"${{args[@]}}\"")]
    ArgsNotPassed { script: PathBuf },
    #[error("cannot run `{script}`: {source}")]
    RunScript {
        script: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("`{script}` failed:\n{stderr}")]
    ScriptFailed { script: PathBuf, stderr: String },
    #[error(transparent)]
    Lake(#[from] LakeError),
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error("cannot read `{path}`: {source}")]
    ReadSource {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error(
        "`{crate_root}` uses `feature = \"lusterna\"`, but `cargo test lusterna_test` ran no tests"
    )]
    NoEquivalenceTests { crate_root: PathBuf },
    #[error("Aeneas wrote `{template}`, but `{external}` does not exist")]
    MissingExternal { template: PathBuf, external: PathBuf },
    #[error("`{path}` contains `{word}` on line {line}")]
    ExternalHole {
        path: PathBuf,
        word: &'static str,
        line: usize,
    },
}

/// The modules for which Aeneas writes a `<module>_Template.lean` file with
/// the declarations that have no body, and which the agent must provide as
/// `<module>.lean`.
const EXTERNAL_MODULES: [&str; 2] = ["TypesExternal", "FunsExternal"];

/// Words that must not appear in the files of `EXTERNAL_MODULES`.
const HOLE_WORDS: [&str; 2] = ["sorry", "admit"];

/// Checks the result of the agent:
///
/// - the extraction script passes the input entrypoints and opaque patterns to
///   Charon
/// - the extraction script runs without error
/// - for every `<module>_Template.lean` of `EXTERNAL_MODULES` in
///   `Extraction/`, the file `<module>.lean` exists and contains no
///   `HOLE_WORDS`
/// - `lake build` succeeds in the lean root
/// - the crate builds without the `lusterna` feature
/// - `cargo test lusterna_test` passes
/// - `cargo test lusterna_test` runs at least one test if the crate uses the
///   `lusterna` feature
pub(super) fn run_gate(
    input: &TranslateInput,
    setup_outcome: &SetupOutcome,
) -> Result<(), TranslateGateErr> {
    let extraction_script = &setup_outcome.extraction_script;
    let lean_root = &setup_outcome.lean_root;
    let crate_root = input.crate_root();

    // The entrypoints and opaque patterns must match the given inputs
    check_patterns(extraction_script, &input.entrypoints, &input.opaque)?;

    // Running the extraction script must succeed
    run_script(extraction_script)?;

    // The declarations without a body must have definitions
    check_externals(&lean_root.join("Extraction"))?;

    // Running `lake build` must succeed in the lean root
    lean::lake_build(lean_root)?;

    // The crate must build without the `lusterna` feature
    let manifest = crate_root.join("Cargo.toml");
    cargo::build(&manifest)?;

    // The equivalence tests must pass, and must exist if the crate uses
    // the `lusterna` feature
    let passed = cargo::test(&manifest, "lusterna_test", EQUIV_TEST_TIMEOUT)?;
    if passed == 0 && uses_lusterna_feature(&crate_root.join("src"))? {
        return Err(TranslateGateErr::NoEquivalenceTests {
            crate_root: crate_root.to_owned(),
        });
    }
    Ok(())
}

impl TranslateGateErr {
    pub(super) fn to_feedback_prompt(&self) -> String {
        match self {
            TranslateGateErr::ReadScript { script, source } => format!(
                "The harness cannot read `{}`: {source}\n\n\
                 Make sure that the file exists and is readable. Do not delete or move it.",
                script.display()
            ),
            TranslateGateErr::MissingLine { script, line } => format!(
                "`{}` must contain this line unchanged:\n\n```\n{line}\n```\n\n\
                 Restore the line. You must not change the entrypoints, the opaque patterns \
                 or the loops that turn them into Charon options.",
                script.display()
            ),
            TranslateGateErr::UnexpectedLine { script, line } => format!(
                "`{}` contains this line, which sets `ENTRYPOINTS` or `OPAQUE` or passes \
                 `--start-from` or `--opaque`:\n\n```\n{line}\n```\n\n\
                 Remove it. Only the lines that setup wrote can set these patterns.",
                script.display()
            ),
            TranslateGateErr::ArgsNotPassed { script } => format!(
                "The `charon` command in `{}` does not pass `\"${{args[@]}}\"`.\n\n\
                 Add it back, so that Charon receives the entrypoints and the opaque patterns.",
                script.display()
            ),
            TranslateGateErr::RunScript { script, source } => format!(
                "The harness cannot run `{}`: {source}\n\n\
                 Make sure that the file exists, is executable and starts with \
                 `#!/usr/bin/env bash`.",
                script.display()
            ),
            TranslateGateErr::ScriptFailed { script, stderr } => format!(
                "`{}` failed with this output:\n\n```\n{stderr}\n```\n\n\
                 Fix the first error and run the script again until it passes.",
                script.display()
            ),
            TranslateGateErr::Lake(LakeError::Build { lean_root, output }) => format!(
                "`lake build` in `{}` failed with this output:\n\n```\n{output}\n```\n\n\
                 Fix the definitions in `Extraction/FunsExternal.lean` and \
                 `Extraction/TypesExternal.lean`, or the Rust code, so that the Lean project \
                 builds. Do not edit the files that Aeneas generates.",
                lean_root.display()
            ),
            TranslateGateErr::Lake(lake_error) => format!(
                "The harness failed to run Lake: {lake_error}\n\n\
                 This is not caused by your changes. Stop and report the error."
            ),
            TranslateGateErr::Cargo(CargoError::Build { manifest, stderr }) => format!(
                "`cargo build` without the `lusterna` feature failed for `{}`:\n\n\
                 ```\n{stderr}\n```\n\n\
                 Your changes must not change the code that the crate builds without the \
                 feature. Fix the `cfg` attributes so that this build works again.",
                manifest.display()
            ),
            TranslateGateErr::Cargo(CargoError::Test {
                manifest,
                filter,
                stdout,
                stderr,
            }) => format!(
                "`cargo test {filter}` failed for `{}`:\n\n```\n{stdout}\n{stderr}\n```\n\n\
                 A failing equivalence test means that a replacement does not behave like the \
                 original. Fix the replacement, not the test, unless the test itself is wrong.",
                manifest.display()
            ),
            TranslateGateErr::Cargo(CargoError::TestTimeout {
                manifest,
                filter,
                timeout,
            }) => format!(
                "`cargo test {filter}` for `{}` did not finish within {timeout:?}.\n\n\
                 Make the equivalence tests faster, for example with fewer random inputs, \
                 and make sure that no test loops forever.",
                manifest.display()
            ),
            TranslateGateErr::Cargo(cargo_error) => format!(
                "The harness failed to run Cargo: {cargo_error}\n\n\
                 This is not caused by your changes. Stop and report the error."
            ),
            TranslateGateErr::ReadSource { path, source } => format!(
                "The harness cannot read `{}`: {source}\n\n\
                 Make sure that the source files of the crate are readable.",
                path.display()
            ),
            TranslateGateErr::NoEquivalenceTests { crate_root } => format!(
                "The crate at `{}` uses `feature = \"lusterna\"`, but `cargo test lusterna_test` \
                 ran no tests.\n\n\
                 Add an equivalence test for every replacement, in a \
                 `#[cfg(test)] mod lusterna_test` next to it.",
                crate_root.display()
            ),
            TranslateGateErr::MissingExternal { template, external } => format!(
                "Aeneas wrote `{}`, but `{}` does not exist.\n\n\
                 Copy the template to `{}` and replace each `axiom` with a definition that \
                 models the Rust item exactly. Keep `axiom` only for items that match an \
                 opaque pattern.",
                template.display(),
                external.display(),
                external.display()
            ),
            TranslateGateErr::ExternalHole { path, word, line } => format!(
                "`{}` contains `{word}` on line {line}.\n\n\
                 Replace it with a complete definition that models the Rust item exactly.",
                path.display()
            ),
        }
    }
}

/// Returns whether a `.rs` file in `dir` or its subdirectories contains
/// `feature = "lusterna"`.
fn uses_lusterna_feature(dir: &Path) -> Result<bool, TranslateGateErr> {
    let read_err = |path: &Path| {
        let path = path.to_owned();
        move |source| TranslateGateErr::ReadSource { path, source }
    };
    for entry in fs::read_dir(dir).map_err(read_err(dir))? {
        let path = entry.map_err(read_err(dir))?.path();
        if path.is_dir() {
            if uses_lusterna_feature(&path)? {
                return Ok(true);
            }
        } else if path.extension().is_some_and(|e| e == "rs") {
            let content = fs::read_to_string(&path).map_err(read_err(&path))?;
            if content.contains(r#"feature = "lusterna""#) {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

/// Checks that the extraction script `script` passes exactly `entrypoints`
/// and `opaque` to Charon. The lines of `make_extraction_script` that set
/// `ENTRYPOINTS` or `OPAQUE` or pass `--start-from` or `--opaque` must appear
/// unchanged, no other line may do so, and the `charon` command must pass
/// `"${args[@]}"`.
fn check_patterns(
    script: &Path,
    entrypoints: &[String],
    opaque: &[String],
) -> Result<(), TranslateGateErr> {
    let content = fs::read_to_string(script).map_err(|source| TranslateGateErr::ReadScript {
        script: script.to_owned(),
        source,
    })?;
    let lines: Vec<&str> = content.lines().map(str::trim).collect();

    let sets_patterns = |line: &str| {
        line.starts_with("ENTRYPOINTS=")
            || line.starts_with("OPAQUE=")
            || line.contains("--start-from")
            || line.contains("--opaque")
    };
    let expected = setup::make_extraction_script(entrypoints, opaque);
    let expected_lines: Vec<&str> = expected.lines().filter(|l| sets_patterns(l)).collect();

    if let Some(line) = expected_lines.iter().find(|l| !lines.contains(l)) {
        return Err(TranslateGateErr::MissingLine {
            script: script.to_owned(),
            line: line.to_string(),
        });
    }
    if let Some(line) = lines
        .iter()
        .find(|l| sets_patterns(l) && !expected_lines.contains(l))
    {
        return Err(TranslateGateErr::UnexpectedLine {
            script: script.to_owned(),
            line: line.to_string(),
        });
    }
    if !lines
        .iter()
        .any(|l| l.starts_with("charon ") && l.contains(r#""${args[@]}""#))
    {
        return Err(TranslateGateErr::ArgsNotPassed {
            script: script.to_owned(),
        });
    }
    Ok(())
}

/// Checks that for every `<module>_Template.lean` of `EXTERNAL_MODULES` in
/// `dir`, the file `<module>.lean` exists and contains none of `HOLE_WORDS`.
fn check_externals(dir: &Path) -> Result<(), TranslateGateErr> {
    for module in EXTERNAL_MODULES {
        let template = dir.join(format!("{module}_Template.lean"));
        if !template.exists() {
            continue;
        }
        let external = dir.join(format!("{module}.lean"));
        if !external.exists() {
            return Err(TranslateGateErr::MissingExternal { template, external });
        }
        let content =
            fs::read_to_string(&external).map_err(|source| TranslateGateErr::ReadSource {
                path: external.clone(),
                source,
            })?;
        for (i, line) in content.lines().enumerate() {
            let words = line
                .split(|c: char| !(c.is_alphanumeric() || c == '_'))
                .collect::<Vec<_>>();
            if let Some(word) = HOLE_WORDS.into_iter().find(|w| words.contains(w)) {
                return Err(TranslateGateErr::ExternalHole {
                    path: external,
                    word,
                    line: i + 1,
                });
            }
        }
    }
    Ok(())
}

/// Runs the extraction script `script` and fails with its stderr if it exits
/// with a non-zero status.
fn run_script(script: &Path) -> Result<(), TranslateGateErr> {
    let output = Command::new(script)
        .output()
        .map_err(|source| TranslateGateErr::RunScript {
            script: script.to_owned(),
            source,
        })?;
    if !output.status.success() {
        return Err(TranslateGateErr::ScriptFailed {
            script: script.to_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::PermissionsExt;

    use super::*;
    use crate::stage::translate::setup::make_extraction_script;

    /// Writes `content` as an executable script into `dir` and returns its
    /// path.
    fn write_script(dir: &Path, content: &str) -> PathBuf {
        let path = dir.join("extract.sh");
        fs::write(&path, content).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    fn patterns() -> (Vec<String>, Vec<String>) {
        (
            vec!["crate::f".to_owned(), "crate::{impl A for B}::g".to_owned()],
            vec!["crate::h".to_owned()],
        )
    }

    #[test]
    fn check_patterns_accepts_generated_script() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let script = write_script(dir.path(), &make_extraction_script(&entrypoints, &opaque));

        check_patterns(&script, &entrypoints, &opaque).unwrap();
    }

    #[test]
    fn check_patterns_accepts_other_charon_options() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content = make_extraction_script(&entrypoints, &opaque).replace(
            "charon cargo --preset=aeneas",
            "charon cargo --preset=aeneas --hide-marker-traits",
        );
        let script = write_script(dir.path(), &content);

        check_patterns(&script, &entrypoints, &opaque).unwrap();
    }

    #[test]
    fn check_patterns_rejects_changed_entrypoints() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let script = write_script(
            dir.path(),
            &make_extraction_script(&["crate::other".to_owned()], &opaque),
        );

        match check_patterns(&script, &entrypoints, &opaque) {
            Err(TranslateGateErr::MissingLine { line, .. }) => {
                assert!(line.starts_with("ENTRYPOINTS="), "{line}")
            }
            other => panic!("expected TranslateGateErr::MissingLine, got {other:?}"),
        }
    }

    #[test]
    fn check_patterns_rejects_extra_opaque_option() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content = make_extraction_script(&entrypoints, &opaque).replace(
            "charon cargo --preset=aeneas",
            "charon cargo --preset=aeneas --opaque crate::x",
        );
        let script = write_script(dir.path(), &content);

        match check_patterns(&script, &entrypoints, &opaque) {
            Err(TranslateGateErr::UnexpectedLine { line, .. }) => {
                assert!(line.starts_with("charon "), "{line}")
            }
            other => panic!("expected TranslateGateErr::UnexpectedLine, got {other:?}"),
        }
    }

    #[test]
    fn check_patterns_rejects_second_array_assignment() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content =
            make_extraction_script(&entrypoints, &opaque).replace("\ncd ", "\nOPAQUE=()\ncd ");
        let script = write_script(dir.path(), &content);

        match check_patterns(&script, &entrypoints, &opaque) {
            Err(TranslateGateErr::UnexpectedLine { line, .. }) => assert_eq!(line, "OPAQUE=()"),
            other => panic!("expected TranslateGateErr::UnexpectedLine, got {other:?}"),
        }
    }

    #[test]
    fn check_patterns_rejects_charon_without_args() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content = make_extraction_script(&entrypoints, &opaque).replace(r#""${args[@]}""#, "");
        let script = write_script(dir.path(), &content);

        assert!(matches!(
            check_patterns(&script, &entrypoints, &opaque),
            Err(TranslateGateErr::ArgsNotPassed { .. })
        ));
    }

    #[test]
    fn run_script_succeeds_on_zero_exit() {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(dir.path(), "#!/bin/sh\nexit 0\n");

        run_script(&script).unwrap();
    }

    #[test]
    fn run_script_reports_stderr_on_failure() {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(dir.path(), "#!/bin/sh\necho oops >&2\nexit 1\n");

        match run_script(&script) {
            Err(TranslateGateErr::ScriptFailed { stderr, .. }) => assert_eq!(stderr, "oops\n"),
            other => panic!("expected TranslateGateErr::ScriptFailed, got {other:?}"),
        }
    }

    #[test]
    fn check_externals_accepts_no_templates() {
        let dir = tempfile::tempdir().unwrap();

        check_externals(dir.path()).unwrap();
    }

    #[test]
    fn check_externals_rejects_missing_external() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("FunsExternal_Template.lean"), "axiom f : Nat\n").unwrap();

        match check_externals(dir.path()) {
            Err(TranslateGateErr::MissingExternal { external, .. }) => {
                assert_eq!(external, dir.path().join("FunsExternal.lean"))
            }
            other => panic!("expected TranslateGateErr::MissingExternal, got {other:?}"),
        }
    }

    #[test]
    fn check_externals_rejects_sorry() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("FunsExternal_Template.lean"), "axiom f : Nat\n").unwrap();
        fs::write(
            dir.path().join("FunsExternal.lean"),
            "-- f\ndef f : Nat := sorry\n",
        )
        .unwrap();

        match check_externals(dir.path()) {
            Err(TranslateGateErr::ExternalHole { word, line, .. }) => {
                assert_eq!((word, line), ("sorry", 2))
            }
            other => panic!("expected TranslateGateErr::ExternalHole, got {other:?}"),
        }
    }

    #[test]
    fn check_externals_accepts_complete_definitions() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("FunsExternal_Template.lean"), "axiom f : Nat\n").unwrap();
        fs::write(
            dir.path().join("FunsExternal.lean"),
            "def f : Nat := 0\ndef sorry_count : Nat := 0\n",
        )
        .unwrap();

        check_externals(dir.path()).unwrap();
    }
}
