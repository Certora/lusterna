use std::{
    ffi::OsStr,
    io,
    path::{Path, PathBuf},
    process::Command,
};

#[derive(Debug, thiserror::Error)]
pub enum GitError {
    #[error("failed to run `git`: {0}")]
    Spawn(#[from] io::Error),
    #[error("`git {args}` failed in `{dir}`:\n{stderr}")]
    Failed {
        dir: PathBuf,
        args: String,
        stderr: String,
    },
}

/// Runs `git <args>` in `dir` and returns its trimmed stdout.
fn run(dir: &Path, args: &[&OsStr]) -> Result<String, GitError> {
    let output = Command::new("git").arg("-C").arg(dir).args(args).output()?;
    if !output.status.success() {
        return Err(GitError::Failed {
            dir: dir.to_owned(),
            args: args
                .iter()
                .map(|a| a.to_string_lossy())
                .collect::<Vec<_>>()
                .join(" "),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

/// Returns the root of the work tree that contains `dir`.
pub fn toplevel(dir: &Path) -> Result<PathBuf, GitError> {
    run(dir, &["rev-parse".as_ref(), "--show-toplevel".as_ref()]).map(PathBuf::from)
}

/// Returns the name of the branch checked out in `repo`. Fails if `HEAD` is
/// detached.
pub fn current_branch(repo: &Path) -> Result<String, GitError> {
    run(
        repo,
        &["symbolic-ref".as_ref(), "--short".as_ref(), "HEAD".as_ref()],
    )
}

/// Returns the commit hash of `HEAD` in `repo`.
pub fn head_rev(repo: &Path) -> Result<String, GitError> {
    run(repo, &["rev-parse".as_ref(), "HEAD".as_ref()])
}

/// Creates the branch `branch` from `start` and checks it out in a new
/// worktree at `path`. Fails if `branch` exists.
pub fn add_worktree(repo: &Path, branch: &str, path: &Path, start: &str) -> Result<(), GitError> {
    run(
        repo,
        &[
            "worktree".as_ref(),
            "add".as_ref(),
            "-b".as_ref(),
            branch.as_ref(),
            path.as_ref(),
            start.as_ref(),
        ],
    )
    .map(|_| ())
}

/// Stages all changes in `repo`, including untracked files that are not
/// ignored, commits them with `message` as `Lusterna <lusterna@agent>`, and
/// returns the commit hash.
pub fn commit_all(repo: &Path, message: &str) -> Result<String, GitError> {
    run(repo, &["add".as_ref(), "--all".as_ref()])?;
    run(
        repo,
        &[
            "-c".as_ref(),
            "user.name=Lusterna".as_ref(),
            "-c".as_ref(),
            "user.email=lusterna@agent".as_ref(),
            "commit".as_ref(),
            "-m".as_ref(),
            message.as_ref(),
        ],
    )?;
    head_rev(repo)
}
