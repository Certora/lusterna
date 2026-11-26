//! The checks that `lusterna` does before it creates a pipeline.
//!
//! A new run starts only if the repository has no uncommitted changes other
//! than the spec file (unless `--allow-dirty`), the tools of `TOOLS` (and on
//! Linux `LINUX_SANDBOX_TOOLS`) are on `PATH`, and the settings that the stages
//! need resolve. `lusterna continue` does not check for uncommitted changes.

use std::{
    env,
    path::{Path, PathBuf},
};

use log::info;

use crate::{
    config::{Config, ConfigError},
    utils::git::{self, GitError},
};

/// The tools that the stages run.
const TOOLS: [&str; 6] = ["git", "cargo", "charon", "aeneas", "lake", "claude"];

/// The tools that the `claude` sandbox needs on Linux.
const LINUX_SANDBOX_TOOLS: [&str; 2] = ["bwrap", "socat"];

/// An error of a check.
#[derive(Debug, thiserror::Error)]
pub enum PrecheckError {
    #[error(transparent)]
    Git(#[from] GitError),
    /// A config value that a stage needs could not be resolved.
    #[error(transparent)]
    Config(#[from] ConfigError),
    /// The repository has uncommitted changes to these files, relative to its
    /// root, other than the spec file.
    #[error(
        "commit or stash the changes to these files before running lusterna: {}",
        .0.iter().map(|f| f.display().to_string()).collect::<Vec<_>>().join(", ")
    )]
    DirtyWorktree(Vec<PathBuf>),
    /// These tools of `TOOLS` or `LINUX_SANDBOX_TOOLS` are not on `PATH`.
    #[error("these tools are not on `PATH`: {}", .0.join(", "))]
    NotInPath(Vec<&'static str>),
}

/// Checks that the repository has no uncommitted changes but the spec at
/// `spec_path`, which the spec commit includes. With `--allow-dirty`, commits
/// the changes instead.
pub fn check_clean_worktree(config: &Config, spec_path: &Path) -> Result<(), PrecheckError> {
    let repo_root = config.repo_root()?;
    let files: Vec<PathBuf> = git::uncommitted_files(&repo_root)?
        .into_iter()
        .filter(|f| repo_root.join(f) != spec_path)
        .collect();
    if files.is_empty() {
        return Ok(());
    }
    if !config.allow_dirty {
        return Err(PrecheckError::DirtyWorktree(files));
    }
    if let Some(sha) = git::commit_all(&repo_root, "Lusterna: uncommitted changes")? {
        info!("Committed the uncommitted changes in {}", sha);
    }
    Ok(())
}

/// Checks that the tools of `TOOLS` (and on Linux `LINUX_SANDBOX_TOOLS`)
/// are on `PATH`, that `aeneas` matches the Aeneas checkout, and that the
/// settings that the stages need resolve.
pub fn check_dependencies(config: &Config) -> Result<(), PrecheckError> {
    let path = env::var_os("PATH").unwrap_or_default();
    let mut tools = TOOLS.to_vec();
    if cfg!(target_os = "linux") {
        tools.extend(LINUX_SANDBOX_TOOLS);
    }
    let missing: Vec<&str> = tools
        .into_iter()
        .filter(|tool| !env::split_paths(&path).any(|dir| dir.join(tool).is_file()))
        .collect();
    if !missing.is_empty() {
        return Err(PrecheckError::NotInPath(missing));
    }
    config.ensure_aeneas_version_match()?;
    config.crate_root()?;
    config.aeneas_rev()?;
    config.lean_toolchain()?;
    config.get_platform()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::{fs, process::Command};

    use super::*;
    use crate::utils::crate_repo;

    #[test]
    fn clean_worktree_passes() {
        let (_dir, root, config) = crate_repo("{}", &[]);
        check_clean_worktree(&config, &root.join("lusterna.json")).unwrap();
    }

    #[test]
    fn changed_spec_file_passes() {
        let (_dir, root, config) = crate_repo("{}", &[]);
        fs::write(root.join("lusterna.json"), r#"{"infer": true}"#).unwrap();
        check_clean_worktree(&config, &root.join("lusterna.json")).unwrap();
    }

    #[test]
    fn other_changes_fail_with_the_changed_files() {
        let (_dir, root, config) = crate_repo("{}", &[]);
        fs::write(root.join("src/lib.rs"), "pub fn f() {}").unwrap();
        fs::write(root.join("new.rs"), "").unwrap();

        let error = check_clean_worktree(&config, &root.join("lusterna.json")).unwrap_err();
        let PrecheckError::DirtyWorktree(mut files) = error else {
            panic!("expected `DirtyWorktree`, got {error}");
        };
        files.sort();
        assert_eq!(
            files,
            [PathBuf::from("new.rs"), PathBuf::from("src/lib.rs")]
        );
    }

    #[test]
    fn allow_dirty_commits_the_changes() {
        let (_dir, root, config) = crate_repo("{}", &["--allow-dirty"]);
        fs::write(root.join("src/lib.rs"), "pub fn f() {}").unwrap();

        check_clean_worktree(&config, &root.join("lusterna.json")).unwrap();
        assert!(git::is_clean(&root).unwrap());
        let log = Command::new("git")
            .args(["log", "-1", "--format=%s"])
            .current_dir(&root)
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8(log.stdout).unwrap().trim(),
            "Lusterna: uncommitted changes"
        );
    }
}
