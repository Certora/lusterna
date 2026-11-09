use std::{
    io,
    path::{Path, PathBuf},
    process::Command,
};

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
/// targets build the default targets.
pub fn lake_build(lean_root: &Path, targets: &[&str]) -> Result<(), LakeError> {
    let output = Command::new("lake")
        .arg("build")
        .args(targets)
        .current_dir(lean_root)
        .output()?;
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
/// its stdout.
pub fn lake_env_lean(lean_root: &Path, file: &Path) -> Result<String, LakeError> {
    let output = Command::new("lake")
        .args(["env", "lean"])
        .arg(file)
        .current_dir(lean_root)
        .output()?;
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
