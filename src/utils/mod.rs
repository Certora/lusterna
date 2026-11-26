use std::{
    fs, io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub mod cargo;
pub mod git;
pub(crate) mod lean;
pub(crate) mod md;
pub mod spec_check;

/// Formats the time elapsed since `t`, rounded down to whole seconds.
pub fn format_elapsed(t: Instant) -> humantime::FormattedDuration {
    humantime::format_duration(Duration::from_secs(t.elapsed().as_secs()))
}

/// Returns `path` relative to the directory `dir`. Both must exist.
pub fn relative_path(dir: &Path, path: &Path) -> Result<PathBuf, io::Error> {
    let dir = fs::canonicalize(dir)?;
    let path = fs::canonicalize(path)?;
    let common = dir
        .components()
        .zip(path.components())
        .take_while(|(a, b)| a == b)
        .count();
    let mut relative = PathBuf::new();
    for _ in common..dir.components().count() {
        relative.push("..");
    }
    for component in path.components().skip(common) {
        relative.push(component);
    }
    Ok(relative)
}

/// Parses `args`, the command-line arguments without the program name, into a
/// `Config`.
#[cfg(test)]
pub(crate) fn parse_config(args: impl IntoIterator<Item = String>) -> crate::config::Config {
    use clap::Parser;

    #[derive(Parser)]
    struct Cli {
        #[command(flatten)]
        config: crate::config::Config,
    }
    Cli::parse_from(std::iter::once("lusterna".to_owned()).chain(args)).config
}

/// Creates a crate at the root of a new repository with one commit, with
/// `spec` as the content of `lusterna.json`. Returns the temporary directory
/// that holds the repository, the root of the repository and the `Config` of
/// the command-line arguments `args`.
#[cfg(test)]
pub(crate) fn crate_repo(
    spec: &str,
    args: &[&str],
) -> (tempfile::TempDir, PathBuf, crate::config::Config) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().canonicalize().unwrap();
    fs::create_dir(root.join("src")).unwrap();
    fs::write(root.join("src/lib.rs"), "").unwrap();
    fs::write(
        root.join("Cargo.toml"),
        "[package]\nname = \"demo\"\nversion = \"0.1.0\"\nedition = \"2024\"\n",
    )
    .unwrap();
    fs::write(root.join("Cargo.lock"), "").unwrap();
    fs::write(root.join("lusterna.json"), spec).unwrap();
    let init = std::process::Command::new("git")
        .args(["init", "-q"])
        .current_dir(&root)
        .status()
        .unwrap();
    assert!(init.success());
    git::commit_all(&root, "init").unwrap();

    let root_arg = root.display().to_string();
    let mut config_args = vec![
        "--workspace-root".to_owned(),
        root_arg.clone(),
        "--crate-root".to_owned(),
        root_arg,
    ];
    config_args.extend(args.iter().map(|a| a.to_string()));
    (dir, root, parse_config(config_args))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relative_path_goes_up_to_the_common_dir() {
        let dir = tempfile::tempdir().unwrap();
        let crate_root = dir.path().join("programs/klend");
        fs::create_dir_all(&crate_root).unwrap();
        fs::write(dir.path().join("Cargo.lock"), "").unwrap();

        let relative = relative_path(&crate_root, &dir.path().join("Cargo.lock")).unwrap();

        assert_eq!(relative, Path::new("../../Cargo.lock"));
    }
}
