use std::{
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};

use crate::{
    stage::{
        self,
        translate::{
            TranslateInput, TranslateStage,
            setup::{self, SetupOutcome},
        },
    },
    utils::{
        cargo::{self, CargoError},
        git::{self, GitError},
        lean::{self, LakeError},
    },
};

/// How long `cargo test lusterna_test` may run in the gate check.
const EQUIV_TEST_TIMEOUT: Duration = Duration::from_secs(10 * 60);

/// How many bytes from the end of the extraction script's stderr the gate
/// keeps.
const EXTRACTION_STDERR_LIMIT: usize = 64 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum TranslateGateError {
    #[error("Error running git: {0}")]
    Git(#[from] GitError),
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("Lake error: {0}")]
    Lake(#[from] LakeError),
    #[error("Cargo error: {0}")]
    Cargo(#[from] CargoError),
}

#[derive(Debug, derive_more::Display)]
pub enum TranslateGateFailure {
    #[display("extraction script misses line: {_0}")]
    ExtractionScriptMissingLine(String),
    #[display("extraction script has unexpected line: {_0}")]
    ExtractionScriptUnexpectedLine(String),
    #[display("extraction script does not pass args to charon")]
    ExtractionScriptArgsNotPassed,
    #[display("extraction script does not pass --release to charon")]
    ExtractionScriptNotRelease,
    #[display("extraction script failed")]
    ExtractionScriptFailed { stderr: String },
    #[display("lake build failed")]
    LakeBuildFailed { output: String },
    #[display("cargo build failed")]
    CargoBuildFailed { stderr: String },
    #[display("equivalence tests failed")]
    EquivalenceTestsFailed { stdout: String, stderr: String },
    #[display("equivalence tests did not finish within {timeout:?}")]
    EquivalenceTestsTimeout { timeout: Duration },
    #[display("no equivalence tests ran")]
    MissingEquivalenceTests,
    #[display("{} tracked files deleted", files.len())]
    DeletedFiles {
        files: Vec<PathBuf>,
        setup_commit: String,
    },
    #[display("missing external file {}", external.display())]
    MissingExternal {
        template: PathBuf,
        external: PathBuf,
    },
    #[display("{word} in {} on line {line}", path.display())]
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

type GateStop = stage::GateStop<TranslateStage>;

impl From<TranslateGateFailure> for GateStop {
    fn from(failure: TranslateGateFailure) -> Self {
        GateStop::Failure(failure)
    }
}

impl<E> From<E> for GateStop
where
    TranslateGateError: From<E>,
{
    fn from(error: E) -> Self {
        GateStop::Error(error.into())
    }
}

/// Checks the result of the agent:
///
/// - no file that the setup commit tracks is deleted
/// - the extraction script passes the input entrypoints and opaque patterns to
///   Charon
/// - the extraction script runs without error
/// - for every `<module>_Template.lean` of `EXTERNAL_MODULES` in `Extraction/`,
///   the file `<module>.lean` exists and contains no `HOLE_WORDS`
/// - `lake build` succeeds in the lean root
/// - the crate builds without the `lusterna` feature
/// - `cargo test lusterna_test` passes
/// - `cargo test lusterna_test` runs at least one test if the crate uses the
///   `lusterna` feature
pub(super) fn run_gate(
    input: &TranslateInput,
    setup_outcome: &SetupOutcome,
) -> Result<(), GateStop> {
    let extraction_script = &setup_outcome.extraction_script;
    let lean_root = &setup_outcome.lean_root;
    let crate_root = input.crate_root();

    // No tracked file may be deleted
    let files = git::deleted_files(crate_root, &setup_outcome.setup_commit)?;
    if !files.is_empty() {
        return Err(TranslateGateFailure::DeletedFiles {
            files,
            setup_commit: setup_outcome.setup_commit.clone(),
        }
        .into());
    }

    // The entrypoints and opaque patterns must match the given inputs
    check_patterns(extraction_script, &input.entrypoints, &input.opaque)?;

    // Running the extraction script must succeed
    run_script(extraction_script)?;

    // The declarations without a body must have definitions
    check_externals(&lean_root.join("Extraction"))?;

    // Running `lake build` must succeed in the lean root
    lean::lake_build(lean_root).map_err(|e| match e {
        LakeError::Build { output, .. } => TranslateGateFailure::LakeBuildFailed { output }.into(),
        e => GateStop::from(e),
    })?;

    // The crate must build without the `lusterna` feature
    let manifest = crate_root.join("Cargo.toml");
    cargo::build(&manifest).map_err(|e| match e {
        CargoError::Build { stderr, .. } => {
            TranslateGateFailure::CargoBuildFailed { stderr }.into()
        }
        e => GateStop::from(e),
    })?;

    // The equivalence tests must pass, and must exist if the crate uses
    // the `lusterna` feature
    let passed =
        cargo::test(&manifest, "lusterna_test", EQUIV_TEST_TIMEOUT).map_err(|e| match e {
            CargoError::Test { stdout, stderr, .. } => {
                TranslateGateFailure::EquivalenceTestsFailed { stdout, stderr }.into()
            }
            CargoError::TestTimeout { timeout, .. } => {
                TranslateGateFailure::EquivalenceTestsTimeout { timeout }.into()
            }
            e => GateStop::from(e),
        })?;
    if passed == 0 && uses_lusterna_feature(&crate_root.join("src"))? {
        return Err(TranslateGateFailure::MissingEquivalenceTests.into());
    }

    Ok(())
}

impl TranslateGateFailure {
    pub(super) fn to_feedback_prompt(&self) -> String {
        match self {
            TranslateGateFailure::ExtractionScriptMissingLine(line) => format!(
                "`extraction.sh` must contain this line unchanged:\n\n```\n{line}\n```\n\n\
                 Restore the line. You must not change the entrypoints, the opaque patterns \
                 or the loops that turn them into Charon options.",
            ),
            TranslateGateFailure::ExtractionScriptUnexpectedLine(line) => format!(
                "`extraction.sh` contains this line, which sets `ENTRYPOINTS` or `OPAQUE` or passes \
                 `--start-from` or `--opaque`:\n\n```\n{line}\n```\n\n\
                 Remove it. Only the lines that setup wrote can set these patterns.",
            ),
            TranslateGateFailure::ExtractionScriptArgsNotPassed => format!(
                "`extraction.sh` has no line that starts with `charon` and passes `\"${{args[@]}}\"`.\n\n\
                 Restore such a line, so that Charon receives the entrypoints and the opaque \
                 patterns. Do not put anything before `charon` on that line. To set an \
                 environment variable, use `export` on an earlier line.",
            ),
            TranslateGateFailure::ExtractionScriptNotRelease => format!(
                "The `charon` command in `extraction.sh` does not pass `--release` to cargo.\n\n\
                 Add `--release` after `--` on that line, so that Charon compiles the crate \
                 with the overflow checks of the release build.",
            ),
            TranslateGateFailure::ExtractionScriptFailed { stderr } => format!(
                "The extraction.sh script failed with this output:\n\n```\n{stderr}\n```\n\n\
                 Fix the first error and run the script again until it passes.",
            ),
            TranslateGateFailure::LakeBuildFailed { output } => format!(
                "`lake build` failed with this output:\n\n```\n{output}\n```\n\n\
                 Fix the first error and run `lake build` again until it passes.",
            ),
            TranslateGateFailure::CargoBuildFailed { stderr } => format!(
                "`cargo build` failed with this output:\n\n```\n{stderr}\n```\n\n\
                 Fix the first error and run `cargo build` again until it passes.",
            ),
            TranslateGateFailure::EquivalenceTestsFailed { stdout, stderr } => format!(
                "`cargo test lusterna_test` failed with this output:\n\n\
                 ```\n{stdout}\n{stderr}\n```\n\n\
                 Fix the first failing test and run `cargo test lusterna_test` again until \
                 it passes.",
            ),
            TranslateGateFailure::EquivalenceTestsTimeout { timeout } => format!(
                "`cargo test lusterna_test` did not finish within {timeout:?}.\n\n\
                 Make the equivalence tests finish within that time.",
            ),
            TranslateGateFailure::MissingEquivalenceTests => format!(
                "The crate uses `feature = \"lusterna\"`, but `cargo test lusterna_test` \
                 ran no tests.\n\n\
                 Add an equivalence test for every replacement, in a \
                 `#[cfg(test)] mod lusterna_test` next to it.",
            ),
            TranslateGateFailure::DeletedFiles {
                files,
                setup_commit,
            } => format!(
                "These files were deleted, but you must not delete files that you did not \
                 create:\n\n{}\n\n\
                 Restore each one with `git restore --source={setup_commit} -- ':/<file>'`.",
                files
                    .iter()
                    .map(|f| format!("- `{}`", f.display()))
                    .collect::<Vec<_>>()
                    .join("\n")
            ),
            TranslateGateFailure::MissingExternal { template, external } => format!(
                "Aeneas wrote `{}`, but `{}` does not exist.\n\n\
                 Copy the template to `{}` and replace each `axiom` with a definition that \
                 models the Rust item exactly. Keep `axiom` only for items that match an \
                 opaque pattern.",
                template.display(),
                external.display(),
                external.display()
            ),
            TranslateGateFailure::ExternalHole { path, word, line } => format!(
                "`{}` contains `{word}` on line {line}.\n\n\
                 Replace it with a complete definition that models the Rust item exactly.",
                path.display()
            ),
        }
    }
}

/// Returns whether a `.rs` file in `dir` or its subdirectories contains
/// `feature = "lusterna"`.
fn uses_lusterna_feature(dir: &Path) -> Result<bool, TranslateGateError> {
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            if uses_lusterna_feature(&path)? {
                return Ok(true);
            }
        } else if path.extension().is_some_and(|e| e == "rs") {
            let content = fs::read_to_string(&path)?;
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
/// `"${args[@]}"` and `--release`.
fn check_patterns(
    script: &Path,
    entrypoints: &[String],
    opaque: &[String],
) -> Result<(), GateStop> {
    let content = fs::read_to_string(script)?;
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
        return Err(GateStop::Failure(
            TranslateGateFailure::ExtractionScriptMissingLine(line.to_string()),
        ));
    }
    if let Some(line) = lines
        .iter()
        .find(|l| sets_patterns(l) && !expected_lines.contains(l))
    {
        return Err(GateStop::Failure(
            TranslateGateFailure::ExtractionScriptUnexpectedLine(line.to_string()),
        ));
    }
    if !lines
        .iter()
        .any(|l| l.starts_with("charon ") && l.contains(r#""${args[@]}""#))
    {
        return Err(GateStop::Failure(
            TranslateGateFailure::ExtractionScriptArgsNotPassed,
        ));
    }
    if !lines
        .iter()
        .any(|l| l.starts_with("charon ") && l.split_whitespace().any(|w| w == "--release"))
    {
        return Err(GateStop::Failure(
            TranslateGateFailure::ExtractionScriptNotRelease,
        ));
    }
    Ok(())
}

/// Checks that for every `<module>_Template.lean` of `EXTERNAL_MODULES` in
/// `dir`, the file `<module>.lean` exists and contains none of `HOLE_WORDS`.
fn check_externals(dir: &Path) -> Result<(), GateStop> {
    for module in EXTERNAL_MODULES {
        let template = dir.join(format!("{module}_Template.lean"));
        if !template.exists() {
            continue;
        }
        let external = dir.join(format!("{module}.lean"));
        if !external.exists() {
            return Err(GateStop::Failure(TranslateGateFailure::MissingExternal {
                template,
                external,
            }));
        }
        let content = fs::read_to_string(&external)?;
        for (i, line) in content.lines().enumerate() {
            let words = line
                .split(|c: char| !(c.is_alphanumeric() || c == '_'))
                .collect::<Vec<_>>();
            if let Some(word) = HOLE_WORDS.into_iter().find(|w| words.contains(w)) {
                return Err(GateStop::Failure(TranslateGateFailure::ExternalHole {
                    path: external,
                    word,
                    line: i + 1,
                }));
            }
        }
    }
    Ok(())
}

/// Runs the extraction script `script` and fails with the end of its stderr if
/// it exits with a non-zero status.
///
/// Charon and Aeneas can print gigabytes of output on a large crate, so stdout
/// is discarded and only the last `EXTRACTION_STDERR_LIMIT` bytes of stderr
/// are kept in memory.
fn run_script(script: &Path) -> Result<(), GateStop> {
    let mut child = Command::new(script)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()?;
    let mut stderr = child.stderr.take().expect("stderr is piped");
    let mut tail = Vec::new();
    let mut buf = [0; 8192];
    loop {
        let n = stderr.read(&mut buf)?;
        if n == 0 {
            break;
        }
        tail.extend_from_slice(&buf[..n]);
        if tail.len() > 2 * EXTRACTION_STDERR_LIMIT {
            tail.drain(..tail.len() - EXTRACTION_STDERR_LIMIT);
        }
    }
    let status = child.wait()?;

    if !status.success() {
        let start = tail.len().saturating_sub(EXTRACTION_STDERR_LIMIT);
        return Err(GateStop::Failure(
            TranslateGateFailure::ExtractionScriptFailed {
                stderr: String::from_utf8_lossy(&tail[start..]).into_owned(),
            },
        ));
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
            Err(GateStop::Failure(TranslateGateFailure::ExtractionScriptMissingLine(line))) => {
                assert!(line.starts_with("ENTRYPOINTS="), "{line}")
            }
            other => panic!("expected ExtractionScriptMissingLine, got {other:?}"),
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
            Err(GateStop::Failure(TranslateGateFailure::ExtractionScriptUnexpectedLine(line))) => {
                assert!(line.starts_with("charon "), "{line}")
            }
            other => panic!("expected ExtractionScriptUnexpectedLine, got {other:?}"),
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
            Err(GateStop::Failure(TranslateGateFailure::ExtractionScriptUnexpectedLine(line))) => {
                assert_eq!(line, "OPAQUE=()")
            }
            other => panic!("expected ExtractionScriptUnexpectedLine, got {other:?}"),
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
            Err(GateStop::Failure(
                TranslateGateFailure::ExtractionScriptArgsNotPassed
            ))
        ));
    }

    #[test]
    fn check_patterns_rejects_charon_without_release() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content = make_extraction_script(&entrypoints, &opaque).replace("--release ", "");
        let script = write_script(dir.path(), &content);

        assert!(matches!(
            check_patterns(&script, &entrypoints, &opaque),
            Err(GateStop::Failure(
                TranslateGateFailure::ExtractionScriptNotRelease
            ))
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
            Err(GateStop::Failure(TranslateGateFailure::ExtractionScriptFailed { stderr })) => {
                assert_eq!(stderr, "oops\n")
            }
            other => panic!("expected ExtractionScriptFailed, got {other:?}"),
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
        fs::write(
            dir.path().join("FunsExternal_Template.lean"),
            "axiom f : Nat\n",
        )
        .unwrap();

        match check_externals(dir.path()) {
            Err(GateStop::Failure(TranslateGateFailure::MissingExternal { external, .. })) => {
                assert_eq!(external, dir.path().join("FunsExternal.lean"))
            }
            other => panic!("expected MissingExternal, got {other:?}"),
        }
    }

    #[test]
    fn check_externals_rejects_sorry() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("FunsExternal_Template.lean"),
            "axiom f : Nat\n",
        )
        .unwrap();
        fs::write(
            dir.path().join("FunsExternal.lean"),
            "-- f\ndef f : Nat := sorry\n",
        )
        .unwrap();

        match check_externals(dir.path()) {
            Err(GateStop::Failure(TranslateGateFailure::ExternalHole { word, line, .. })) => {
                assert_eq!((word, line), ("sorry", 2))
            }
            other => panic!("expected ExternalHole, got {other:?}"),
        }
    }

    #[test]
    fn check_externals_accepts_complete_definitions() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("FunsExternal_Template.lean"),
            "axiom f : Nat\n",
        )
        .unwrap();
        fs::write(
            dir.path().join("FunsExternal.lean"),
            "def f : Nat := 0\ndef sorry_count : Nat := 0\n",
        )
        .unwrap();

        check_externals(dir.path()).unwrap();
    }
}
