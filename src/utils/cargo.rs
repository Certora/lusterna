use std::{
    fs,
    io::{self, Read},
    os::unix::process::CommandExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use toml_edit::{Array, DocumentMut, TomlError};

#[derive(Debug, thiserror::Error)]
pub enum CargoError {
    #[error("failed to run `cargo`: {0}")]
    Spawn(#[from] io::Error),
    #[error("cannot find the workspace of `{manifest}`:\n{stderr}")]
    LocateProject { manifest: PathBuf, stderr: String },
    #[error("`cargo build` failed for `{manifest}`:\n{stderr}")]
    Build { manifest: PathBuf, stderr: String },
    #[error("`cargo test {filter}` failed for `{manifest}`:\n{stdout}\n{stderr}")]
    Test {
        manifest: PathBuf,
        filter: String,
        stdout: String,
        stderr: String,
    },
    #[error("`cargo test {filter}` for `{manifest}` did not finish within {timeout:?}")]
    TestTimeout {
        manifest: PathBuf,
        filter: String,
        timeout: Duration,
    },
    #[error("cannot read `{manifest}`: {source}")]
    ReadManifest {
        manifest: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("cannot parse `{manifest}`: {source}")]
    ParseManifest {
        manifest: PathBuf,
        #[source]
        source: TomlError,
    },
    #[error("`features` in `{manifest}` is not a table")]
    FeaturesNotTable { manifest: PathBuf },
    #[error("cannot write `{manifest}`: {source}")]
    WriteManifest {
        manifest: PathBuf,
        #[source]
        source: io::Error,
    },
}

/// Returns the path of the workspace `Cargo.toml` that `manifest` belongs to.
pub fn workspace_manifest(manifest: &Path) -> Result<PathBuf, CargoError> {
    let output = Command::new("cargo")
        .args(["locate-project", "--workspace", "--message-format", "plain"])
        .arg("--manifest-path")
        .arg(manifest)
        .output()?;
    if !output.status.success() {
        return Err(CargoError::LocateProject {
            manifest: manifest.to_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    Ok(PathBuf::from(
        String::from_utf8_lossy(&output.stdout).trim(),
    ))
}

/// Builds the package of `manifest`.
pub fn build(manifest: &Path) -> Result<(), CargoError> {
    let output = Command::new("cargo")
        .arg("build")
        .arg("--manifest-path")
        .arg(manifest)
        .output()?;
    if !output.status.success() {
        return Err(CargoError::Build {
            manifest: manifest.to_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    Ok(())
}

/// Runs `cargo test <filter>` for the package of `manifest` and returns the
/// number of tests that passed. Kills `cargo` and the test binaries if they
/// run longer than `timeout`.
pub fn test(manifest: &Path, filter: &str, timeout: Duration) -> Result<usize, CargoError> {
    let mut child = Command::new("cargo")
        .arg("test")
        .arg("--manifest-path")
        .arg(manifest)
        .arg(filter)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()?;
    let read = |mut pipe: Box<dyn Read + Send>| {
        thread::spawn(move || {
            let mut text = String::new();
            let _ = pipe.read_to_string(&mut text);
            text
        })
    };
    let stdout = read(Box::new(child.stdout.take().unwrap()));
    let stderr = read(Box::new(child.stderr.take().unwrap()));

    let start = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if start.elapsed() >= timeout {
            Command::new("kill")
                .arg("-KILL")
                .arg(format!("-{}", child.id()))
                .status()?;
            child.wait()?;
            return Err(CargoError::TestTimeout {
                manifest: manifest.to_owned(),
                filter: filter.to_owned(),
                timeout,
            });
        }
        thread::sleep(Duration::from_millis(100));
    };
    let stdout = stdout.join().unwrap();
    let stderr = stderr.join().unwrap();

    if !status.success() {
        return Err(CargoError::Test {
            manifest: manifest.to_owned(),
            filter: filter.to_owned(),
            stdout,
            stderr,
        });
    }
    // Each test binary prints `test result: ok. <n> passed; ...`.
    Ok(stdout
        .lines()
        .filter_map(|l| l.strip_prefix("test result: ok. "))
        .filter_map(|l| l.split_once(" passed")?.0.parse::<usize>().ok())
        .sum())
}

/// Adds the feature `name` with no dependencies to `manifest`, unless it
/// already has a feature of that name.
pub fn add_feature(manifest: &Path, name: &str) -> Result<(), CargoError> {
    let text = fs::read_to_string(manifest).map_err(|source| CargoError::ReadManifest {
        manifest: manifest.to_owned(),
        source,
    })?;
    let mut doc: DocumentMut = text.parse().map_err(|source| CargoError::ParseManifest {
        manifest: manifest.to_owned(),
        source,
    })?;

    let features = doc
        .entry("features")
        .or_insert(toml_edit::table())
        .as_table_like_mut()
        .ok_or_else(|| CargoError::FeaturesNotTable {
            manifest: manifest.to_owned(),
        })?;
    if features.contains_key(name) {
        return Ok(());
    }
    features.insert(name, toml_edit::value(Array::new()));

    fs::write(manifest, doc.to_string()).map_err(|source| CargoError::WriteManifest {
        manifest: manifest.to_owned(),
        source,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Writes a library crate named `name` with `src/lib.rs` set to `lib_rs`
    /// into `dir` and returns the path to its `Cargo.toml`.
    fn write_crate(dir: &Path, name: &str, lib_rs: &str) -> PathBuf {
        fs::create_dir_all(dir.join("src")).unwrap();
        fs::write(dir.join("src/lib.rs"), lib_rs).unwrap();
        let manifest = dir.join("Cargo.toml");
        fs::write(
            &manifest,
            format!("[package]\nname = \"{name}\"\nversion = \"0.1.0\"\nedition = \"2024\"\n"),
        )
        .unwrap();
        manifest
    }

    #[test]
    fn workspace_manifest_of_standalone_crate_is_itself() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = write_crate(dir.path(), "solo", "");

        let found = workspace_manifest(&manifest).unwrap();
        assert_eq!(
            fs::canonicalize(found).unwrap(),
            fs::canonicalize(manifest).unwrap()
        );
    }

    #[test]
    fn workspace_manifest_of_member_is_workspace_root() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("Cargo.toml");
        fs::write(&root, "[workspace]\nmembers = [\"member\"]\n").unwrap();
        let member = write_crate(&dir.path().join("member"), "member", "");

        let found = workspace_manifest(&member).unwrap();
        assert_eq!(
            fs::canonicalize(found).unwrap(),
            fs::canonicalize(root).unwrap()
        );
    }

    #[test]
    fn build_succeeds_on_valid_crate() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = write_crate(dir.path(), "ok", "pub fn f() {}\n");

        build(&manifest).unwrap();
    }

    #[test]
    fn add_feature_creates_features_table() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = write_crate(dir.path(), "a", "");

        add_feature(&manifest, "lusterna").unwrap();

        let doc: DocumentMut = fs::read_to_string(&manifest).unwrap().parse().unwrap();
        let feature = doc["features"]["lusterna"].as_array().unwrap();
        assert!(feature.is_empty());
    }

    #[test]
    fn add_feature_keeps_existing_features_and_comments() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = write_crate(dir.path(), "a", "");
        let mut text = fs::read_to_string(&manifest).unwrap();
        text.push_str("\n[features]\n# a comment\nother = []\n");
        fs::write(&manifest, &text).unwrap();

        add_feature(&manifest, "lusterna").unwrap();

        let new_text = fs::read_to_string(&manifest).unwrap();
        assert!(new_text.starts_with(&text), "{new_text}");
        let doc: DocumentMut = new_text.parse().unwrap();
        assert!(doc["features"]["lusterna"].as_array().unwrap().is_empty());
    }

    #[test]
    fn add_feature_leaves_existing_feature_unchanged() {
        let dir = tempfile::tempdir().unwrap();
        let manifest = write_crate(dir.path(), "a", "");
        let mut text = fs::read_to_string(&manifest).unwrap();
        text.push_str("\n[features]\nlusterna = [\"dep/x\"]\n");
        fs::write(&manifest, &text).unwrap();

        add_feature(&manifest, "lusterna").unwrap();

        assert_eq!(fs::read_to_string(&manifest).unwrap(), text);
    }
}
