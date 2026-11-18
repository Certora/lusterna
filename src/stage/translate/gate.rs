use std::{
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};

use serde::{Deserialize, Serialize};

use crate::{
    stage::translate::{TranslateInput, setup::TranslateSetupOutcome},
    utils::{
        cargo::{self, CargoError},
        git::{self, GitError},
        lean::{self, LakeError},
    },
};

/// How long `cargo test lusterna::` may run in the gate check.
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

#[derive(Debug, derive_more::Display, Serialize, Deserialize, Clone)]
pub enum TranslateGateFailure {
    #[display("extraction script changed")]
    ExtractionScriptChanged { setup_commit: String },
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
        word: String,
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
/// - no file that the setup commit tracks is deleted
/// - the extraction script is unchanged since the setup commit
/// - the extraction script runs without error
/// - for every `<module>_Template.lean` of `EXTERNAL_MODULES` in `Extraction/`,
///   the file `<module>.lean` exists and contains no `HOLE_WORDS`
/// - `lake build` succeeds in the lean root
/// - the crate builds without the `lusterna` feature
/// - `cargo test lusterna::` passes
/// - `cargo test lusterna::` runs at least one test if the crate uses the
///   `lusterna` feature
pub(super) fn run_gate(
    input: &TranslateInput,
    setup_outcome: &TranslateSetupOutcome,
) -> Result<Option<TranslateGateFailure>, TranslateGateError> {
    let extraction_script = &setup_outcome.extraction_script;
    let lean_root = &setup_outcome.lean_root;
    let crate_root = &input.crate_root;

    // No tracked file may be deleted
    let files = git::deleted_files(crate_root, &setup_outcome.setup_commit)?;
    if !files.is_empty() {
        return Ok(Some(TranslateGateFailure::DeletedFiles {
            files,
            setup_commit: setup_outcome.setup_commit.clone(),
        }));
    }

    // The extraction script must be unchanged since the setup commit
    let repo_root = git::toplevel(crate_root)?;
    let changed = git::changed_files(crate_root, &setup_outcome.setup_commit)?;
    if changed
        .iter()
        .any(|f| repo_root.join(f) == *extraction_script)
    {
        return Ok(Some(TranslateGateFailure::ExtractionScriptChanged {
            setup_commit: setup_outcome.setup_commit.clone(),
        }));
    }

    // Running the extraction script must succeed
    if let Some(failure) = run_script(extraction_script)? {
        return Ok(Some(failure));
    }

    // The declarations without a body must have definitions
    if let Some(failure) = check_externals(&lean_root.join("Extraction"))? {
        return Ok(Some(failure));
    }

    // Running `lake build` must succeed in the lean root
    match lean::lake_build(lean_root, &[]) {
        Ok(_) => (),
        Err(LakeError::Build { output, .. }) => {
            return Ok(Some(TranslateGateFailure::LakeBuildFailed { output }));
        }
        Err(e) => return Err(TranslateGateError::from(e)),
    }

    // The crate must build without the `lusterna` feature
    let manifest = crate_root.join("Cargo.toml");
    match cargo::build(&manifest) {
        Ok(_) => (),
        Err(CargoError::Build { stderr, .. }) => {
            return Ok(Some(TranslateGateFailure::CargoBuildFailed { stderr }));
        }
        Err(e) => return Err(e.into()),
    }

    // The equivalence tests must pass, and must exist if the crate uses
    // the `lusterna` feature
    let passed = match cargo::test(&manifest, "lusterna::", EQUIV_TEST_TIMEOUT) {
        Ok(n) => n,
        Err(CargoError::Test { stdout, stderr, .. }) => {
            return Ok(Some(TranslateGateFailure::EquivalenceTestsFailed {
                stdout,
                stderr,
            }));
        }
        Err(CargoError::TestTimeout { timeout, .. }) => {
            return Ok(Some(TranslateGateFailure::EquivalenceTestsTimeout {
                timeout,
            }));
        }
        Err(e) => return Err(e.into()),
    };

    if passed == 0 && uses_lusterna_feature(&crate_root.join("src"))? {
        return Ok(Some(TranslateGateFailure::MissingEquivalenceTests));
    }

    Ok(None)
}

impl TranslateGateFailure {
    pub(super) fn to_feedback_prompt(&self) -> String {
        match self {
            TranslateGateFailure::ExtractionScriptChanged { setup_commit } => format!(
                "`lusterna/extract.sh` differs from the version that setup wrote, but you must \
                 not edit it.\n\n\
                 Restore it with `git restore --source={setup_commit} -- lusterna/extract.sh`. \
                 Put extra Charon, cargo and Aeneas arguments in `lusterna/extract-args.sh`.",
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
                "`cargo test lusterna::` failed with this output:\n\n\
                 ```\n{stdout}\n{stderr}\n```\n\n\
                 Fix the first failing test and run `cargo test lusterna::` again until \
                 it passes.",
            ),
            TranslateGateFailure::EquivalenceTestsTimeout { timeout } => format!(
                "`cargo test lusterna::` did not finish within {timeout:?}.\n\n\
                 Make the equivalence tests finish within that time.",
            ),
            TranslateGateFailure::MissingEquivalenceTests => {
                "The crate uses `feature = \"lusterna\"`, but `cargo test lusterna::` \
                 ran no tests.\n\n\
                 Add an equivalence test for every replacement, in the \
                 `#[cfg(test)] mod tests` of the `lusterna` module that contains it."
                    .to_string()
            }
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

/// Checks that for every `<module>_Template.lean` of `EXTERNAL_MODULES` in
/// `dir`, the file `<module>.lean` exists and contains none of `HOLE_WORDS`.
fn check_externals(dir: &Path) -> Result<Option<TranslateGateFailure>, TranslateGateError> {
    for module in EXTERNAL_MODULES {
        let template = dir.join(format!("{module}_Template.lean"));
        if !template.exists() {
            continue;
        }
        let external = dir.join(format!("{module}.lean"));
        if !external.exists() {
            return Ok(Some(TranslateGateFailure::MissingExternal {
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
                return Ok(Some(TranslateGateFailure::ExternalHole {
                    path: external,
                    word: word.to_owned(),
                    line: i + 1,
                }));
            }
        }
    }
    Ok(None)
}

/// Runs the extraction script `script` and fails with the end of its stderr if
/// it exits with a non-zero status.
///
/// Charon and Aeneas can print gigabytes of output on a large crate, so stdout
/// is discarded and only the last `EXTRACTION_STDERR_LIMIT` bytes of stderr
/// are kept in memory.
#[must_use]
fn run_script(script: &Path) -> Result<Option<TranslateGateFailure>, TranslateGateError> {
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
        return Ok(Some(TranslateGateFailure::ExtractionScriptFailed {
            stderr: String::from_utf8_lossy(&tail[start..]).into_owned(),
        }));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::PermissionsExt;

    use super::*;

    /// Writes `content` as an executable script into `dir` and returns its
    /// path.
    fn write_script(dir: &Path, content: &str) -> PathBuf {
        let path = dir.join("extract.sh");
        fs::write(&path, content).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    #[test]
    fn run_script_succeeds_on_zero_exit() {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(dir.path(), "#!/bin/sh\nexit 0\n");

        assert!(run_script(&script).unwrap().is_none());
    }

    #[test]
    fn run_script_reports_stderr_on_failure() {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(dir.path(), "#!/bin/sh\necho oops >&2\nexit 1\n");

        match run_script(&script) {
            Ok(Some(TranslateGateFailure::ExtractionScriptFailed { stderr })) => {
                assert_eq!(stderr, "oops\n")
            }
            other => panic!("expected ExtractionScriptFailed, got {other:?}"),
        }
    }

    #[test]
    fn check_externals_accepts_no_templates() {
        let dir = tempfile::tempdir().unwrap();

        assert!(check_externals(dir.path()).unwrap().is_none());
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
            Ok(Some(TranslateGateFailure::MissingExternal { external, .. })) => {
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
            Ok(Some(TranslateGateFailure::ExternalHole { word, line, .. })) => {
                assert_eq!((word.as_str(), line), ("sorry", 2))
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

        assert!(check_externals(dir.path()).unwrap().is_none());
    }
}
