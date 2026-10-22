use std::{
    fs, io,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};

use crate::{
    stage::translate::TranslateInput,
    utils::{
        cargo::{self, CargoError},
        lean::{self, LakeError},
    },
};

/// The extraction script `lusterna/extract.sh`, with `{entrypoints}` and
/// `{opaque}` standing for the bash-quoted patterns.
const EXTRACT_SH_TEMPLATE: &str = include_str!("res/extract.sh.template");

/// The `lakefile.toml` of the Lean project, with `{rev}` standing for the
/// Aeneas git revision. Aeneas names the generated module after the `.llbc`
/// file in CamelCase, and `extract.sh` writes `extraction.llbc`.
const LAKEFILE_TEMPLATE: &str = include_str!("res/lakefile.toml.template");

#[derive(Debug, thiserror::Error)]
pub enum TranslateSetupErr {
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("`{0}` is not the root of a Cargo workspace")]
    NotWorkspaceRoot(PathBuf),
    #[error("`{crate_root}` is not a crate in the workspace `{workspace_root}`")]
    NotWorkspaceMember {
        crate_root: PathBuf,
        workspace_root: PathBuf,
    },
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error(transparent)]
    Lake(#[from] LakeError),
}

/// The paths that setup created or found in the crate root.
#[derive(Debug, Clone)]
pub(super) struct SetupOutcome {
    /// Absolute path to `lusterna/extract.sh`
    pub extraction_script: PathBuf,
    /// Whether `lusterna/extract.sh` existed before setup
    pub extraction_existed: bool,
    /// Absolute path to the lean project root dir `lusterna/lean`
    pub lean_root: PathBuf,
    /// Whether `lusterna/lean/lakefile.toml` existed before setup
    pub lean_existed: bool,
}

/// Sets up the crate for the agent:
///
/// - checks that `input.workspace_root` is the root of a Cargo workspace
/// - checks that the crate root is a crate in that workspace
/// - builds the crate
/// - adds the `lusterna` feature to the crate
/// - creates the extraction script if it does not exist
/// - creates the lean project if it does not exist
pub(super) fn run_setup(input: &TranslateInput) -> Result<SetupOutcome, TranslateSetupErr> {
    let workspace_root = &input.workspace_root;
    let crate_root = input.crate_root();
    // Check if workspace root is actually a rust workspace
    let ws_manifest = fs::canonicalize(workspace_root.join("Cargo.toml"))
        .map_err(|_| TranslateSetupErr::NotWorkspaceRoot(workspace_root.to_owned()))?;
    if fs::canonicalize(cargo::workspace_manifest(&ws_manifest)?)? != ws_manifest {
        return Err(TranslateSetupErr::NotWorkspaceRoot(
            workspace_root.to_owned(),
        ));
    }

    // If crate root given: Check if it is a crate within the workspace and that it
    // compiles/build
    // If crate root not given: Check that the workspace is a crate root and
    // that it compiles/builds
    let crate_manifest = crate_root.join("Cargo.toml");
    if !crate_manifest.exists()
        || fs::canonicalize(cargo::workspace_manifest(&crate_manifest)?)? != ws_manifest
    {
        return Err(TranslateSetupErr::NotWorkspaceMember {
            crate_root: crate_root.to_owned(),
            workspace_root: workspace_root.to_owned(),
        });
    }
    cargo::build(&crate_manifest)?;
    cargo::add_feature(&crate_manifest, "lusterna")?;

    let (extraction_script_path, extraction_script_created) =
        create_extraction_script(crate_root, &input.entrypoints, &input.opaque)?;

    let (lean_root, lean_root_existed) =
        init_lake_project(crate_root, &input.aeneas_rev, &input.lean_toolchain)?;

    Ok(SetupOutcome {
        extraction_script: extraction_script_path,
        extraction_existed: !extraction_script_created,
        lean_root: lean_root,
        lean_existed: lean_root_existed,
    })
}

/// Returns the content of `lusterna/extract.sh` for the given Charon
/// `--start-from` and `--opaque` patterns.
pub(super) fn make_extraction_script(entrypoints: &[String], opaque: &[String]) -> String {
    let quote = |patterns: &[String]| {
        patterns
            .iter()
            .map(|p| format!("'{}'", p.replace('\'', r"'\''")))
            .collect::<Vec<_>>()
            .join(" ")
    };
    EXTRACT_SH_TEMPLATE
        .replace("{entrypoints}", &quote(entrypoints))
        .replace("{opaque}", &quote(opaque))
}

/// Returns the content of the Lean project's `lakefile.toml` for the given
/// Aeneas git revision.
fn make_lakefile(aeneas_rev: &str) -> String {
    LAKEFILE_TEMPLATE.replace("{rev}", aeneas_rev)
}

/// Creates the lean project in `lusterna/lean` of the crate root:
/// - writes a `lakefile.toml`
/// - sets up the dependency to Aeneas for the needed ref
/// - wires the extraction file(s) (possibly not existing yet) to be part of the
///   project
/// - writes the `lean-toolchain` and calls `lake update`
///
/// Returns a tuple with the first element the path to the lean project root
/// and the second a flag indicating whether a project was actually
/// initialized: `true` if it was and `false` if it already existed.
/// Returns an error if failed.
///
/// TODO: this fails if the existing lakefile states other dependencies
/// or a different Aeneas version.
fn init_lake_project(
    crate_root: &Path,
    aeneas_rev: &str,
    lean_toolchain: &str,
) -> Result<(PathBuf, bool), TranslateSetupErr> {
    let lean_root = crate_root.join("lusterna").join("lean");
    if lean_root.join("lakefile.toml").exists() {
        return Ok((lean_root, false));
    }

    fs::create_dir_all(&lean_root)?;
    fs::write(lean_root.join("lakefile.toml"), make_lakefile(aeneas_rev))?;
    fs::write(
        lean_root.join("lean-toolchain"),
        format!("{}\n", lean_toolchain.trim()),
    )?;

    lean::lake_update(&lean_root)?;

    Ok((lean_root, true))
}

/// Creates the initial extraction script.
/// Returns a tuple with the first element the path to the script and the
/// second a flag indicating whether the script was created or already
/// existed: `true` if it was created and `false` if it already existed.
/// Returns an error if creation failed
///
/// TODO: this fails if the existing script uses different entry points than
/// the one we specified here
fn create_extraction_script(
    crate_root: &Path,
    entrypoints: &[String],
    opaque: &[String],
) -> Result<(PathBuf, bool), io::Error> {
    let path = crate_root.join("lusterna").join("extract.sh");
    if path.exists() {
        return Ok((path, false));
    }

    fs::create_dir_all(path.parent().unwrap())?;
    fs::write(&path, make_extraction_script(entrypoints, opaque))?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755))?;
    Ok((path, true))
}

#[cfg(test)]
mod tests {
    use std::process::Command;

    use toml_edit::DocumentMut;

    use super::*;

    /// Runs the part of `script` before the `cd` line in bash and returns the
    /// elements of the bash array `array`.
    fn bash_array(script: &str, array: &str) -> Vec<String> {
        let (definitions, _) = script.split_once("\ncd ").unwrap();
        let output = Command::new("bash")
            .arg("-c")
            .arg(format!(
                "{definitions}\nfor x in \"${{{array}[@]}}\"; do printf '%s\\0' \"$x\"; done"
            ))
            .output()
            .unwrap();
        assert!(output.status.success(), "{output:?}");
        String::from_utf8(output.stdout)
            .unwrap()
            .split_terminator('\0')
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn extract_sh_keeps_patterns_intact() {
        let entrypoints = vec![
            "crate::f".to_owned(),
            "crate::{impl Foo for Bar}::g".to_owned(),
            "it's $HOME `x` \"y\"".to_owned(),
        ];
        let opaque = vec!["crate::a b".to_owned(), "'".to_owned()];

        let script = make_extraction_script(&entrypoints, &opaque);

        assert_eq!(bash_array(&script, "ENTRYPOINTS"), entrypoints);
        assert_eq!(bash_array(&script, "OPAQUE"), opaque);
    }

    #[test]
    fn extract_sh_allows_empty_patterns() {
        let script = make_extraction_script(&[], &[]);

        assert!(bash_array(&script, "ENTRYPOINTS").is_empty());
        assert!(bash_array(&script, "OPAQUE").is_empty());
    }

    #[test]
    fn lakefile_requires_aeneas_at_rev() {
        let doc: DocumentMut = make_lakefile("abc123").parse().unwrap();

        let aeneas = &doc["require"][0];
        assert_eq!(aeneas["name"].as_str(), Some("aeneas"));
        assert_eq!(aeneas["rev"].as_str(), Some("abc123"));
        assert_eq!(aeneas["subDir"].as_str(), Some("backends/lean"));
    }
}
