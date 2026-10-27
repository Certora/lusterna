use std::{
    fs, io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub mod cargo;
pub mod git;
pub(crate) mod lean;

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
