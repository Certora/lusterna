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
}

/// Runs `lake build` in the Lean project at `lean_root`.
pub fn lake_build(lean_root: &Path) -> Result<(), LakeError> {
    let output = Command::new("lake")
        .arg("build")
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
    #[cfg_attr(feature = "no-lake", ignore = "needs `lake`, skipped by the `no-lake` feature")]
    fn lake_update_succeeds_on_valid_project() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("lakefile.toml"), "name = \"test\"\n").unwrap();

        lake_update(dir.path()).unwrap();
    }
}
