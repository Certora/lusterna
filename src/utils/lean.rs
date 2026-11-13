use std::{
    io::{self, Read, Seek, SeekFrom},
    os::unix::process::CommandExt,
    path::{Path, PathBuf},
    process::{Command, Output, Stdio},
    thread,
    time::{Duration, Instant},
};

/// How long `lake build` and `lake env lean` may run.
const LAKE_TIME_LIMIT: Duration = Duration::from_secs(30 * 60);

/// Runs `command` and returns its output. Returns `None` if `command` runs
/// longer than `limit`, after killing it.
fn output_within(command: &mut Command, limit: Duration) -> io::Result<Option<Output>> {
    // The child writes to files, so that it never blocks on a full pipe
    let mut stdout = tempfile::tempfile()?;
    let mut stderr = tempfile::tempfile()?;
    let mut child = command
        .stdin(Stdio::null())
        .stdout(stdout.try_clone()?)
        .stderr(stderr.try_clone()?)
        .process_group(0)
        .spawn()?;

    // Wait until the child exits or the time is up
    let start = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if start.elapsed() >= limit {
            // The child is the leader of its process group, which also holds
            // the processes that the child started
            Command::new("kill")
                .args(["-KILL", "--", &format!("-{}", child.id())])
                .status()?;
            child.wait()?;
            return Ok(None);
        }
        thread::sleep(Duration::from_millis(100));
    };

    // Read what the child wrote
    let mut output = Output {
        status,
        stdout: Vec::new(),
        stderr: Vec::new(),
    };
    stdout.seek(SeekFrom::Start(0))?;
    stdout.read_to_end(&mut output.stdout)?;
    stderr.seek(SeekFrom::Start(0))?;
    stderr.read_to_end(&mut output.stderr)?;
    Ok(Some(output))
}

/// The output that `lake_build` and `lake_env_lean` report for a run that
/// `output_within` stopped.
fn time_limit_message() -> String {
    format!(
        "The command did not finish within {} and was stopped.",
        humantime::format_duration(LAKE_TIME_LIMIT)
    )
}

#[derive(Debug, thiserror::Error)]
pub enum LakeError {
    #[error("failed to run `lake`: {0}")]
    Spawn(#[from] io::Error),
    #[error("`lake update` failed in `{lean_root}`:\n{stderr}")]
    Update { lean_root: PathBuf, stderr: String },
    #[error("`lake build` failed in `{lean_root}`:\n{output}")]
    Build { lean_root: PathBuf, output: String },
    #[error("`lake env lean {file}` failed:\n{output}")]
    Lean { file: PathBuf, output: String },
}

/// Runs `lake build` with `targets` in the Lean project at `lean_root`. No
/// targets build the default targets. Fails if the build runs longer than
/// `LAKE_TIME_LIMIT`.
pub fn lake_build(lean_root: &Path, targets: &[&str]) -> Result<(), LakeError> {
    let mut command = Command::new("lake");
    command.arg("build").args(targets).current_dir(lean_root);
    let Some(output) = output_within(&mut command, LAKE_TIME_LIMIT)? else {
        return Err(LakeError::Build {
            lean_root: lean_root.to_owned(),
            output: time_limit_message(),
        });
    };
    if !output.status.success() {
        return Err(LakeError::Build {
            lean_root: lean_root.to_owned(),
            output: format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            ),
        });
    }
    Ok(())
}

/// Runs `lake env lean <file>` in the Lean project at `lean_root` and returns
/// its stdout. Fails if it runs longer than `LAKE_TIME_LIMIT`.
pub fn lake_env_lean(lean_root: &Path, file: &Path) -> Result<String, LakeError> {
    let mut command = Command::new("lake");
    command
        .args(["env", "lean"])
        .arg(file)
        .current_dir(lean_root);
    let Some(output) = output_within(&mut command, LAKE_TIME_LIMIT)? else {
        return Err(LakeError::Lean {
            file: file.to_owned(),
            output: time_limit_message(),
        });
    };
    if !output.status.success() {
        return Err(LakeError::Lean {
            file: file.to_owned(),
            output: format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            ),
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Runs `lake update` in the Lean project at `lean_root`.
pub fn lake_update(lean_root: &Path) -> Result<(), LakeError> {
    let output = Command::new("lake")
        .arg("update")
        .current_dir(lean_root)
        .output()?;
    if !output.status.success() {
        return Err(LakeError::Update {
            lean_root: lean_root.to_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    #[test]
    fn output_within_returns_the_output() {
        let mut command = Command::new("sh");
        command.args(["-c", "echo out; echo err >&2; exit 3"]);

        let output = output_within(&mut command, Duration::from_secs(10))
            .unwrap()
            .unwrap();

        assert_eq!(output.status.code(), Some(3));
        assert_eq!(output.stdout, b"out\n");
        assert_eq!(output.stderr, b"err\n");
    }

    #[test]
    fn output_within_kills_the_process_group_at_the_limit() {
        let dir = tempfile::tempdir().unwrap();
        let marker = dir.path().join("marker");
        // The child starts a second process that writes `marker` after 2s
        let script = format!("(sleep 2; touch '{}') & sleep 30", marker.display());
        let mut command = Command::new("sh");
        command.args(["-c", &script]);

        let start = Instant::now();
        let output = output_within(&mut command, Duration::from_millis(300)).unwrap();

        assert!(output.is_none());
        assert!(start.elapsed() < Duration::from_secs(2));
        thread::sleep(Duration::from_secs(3));
        assert!(!marker.exists());
    }

    #[test]
    #[cfg_attr(
        feature = "no-lake",
        ignore = "needs `lake`, skipped by the `no-lake` feature"
    )]
    fn lake_update_succeeds_on_valid_project() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("lakefile.toml"), "name = \"test\"\n").unwrap();

        lake_update(dir.path()).unwrap();
    }
}
