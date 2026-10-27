use std::{
    env, fs, io,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};

use crate::{
    config::Platform,
    stage::translate::{TranslateInput, replacements, translate_info},
    utils::{
        cargo::{self, CargoError},
        git::{self, GitError},
        lean::{self, LakeError},
    },
};

/// The extraction script `lusterna/extract.sh`, with `{entrypoints}`,
/// `{opaque}`, `{charon_args}` and `{cargo_args}` standing for the
/// bash-quoted patterns and the extra `charon` and `cargo` arguments, and
/// `{charon_lock}` standing for the line that creates the `Cargo.lock` of the
/// Charon build, or nothing.
const EXTRACT_SH_TEMPLATE: &str = include_str!("res/extract.sh.template");

/// The `Cargo.lock` that the Charon build of a Solana crate uses, relative to
/// the crate root. `extract.sh` copies the workspace `Cargo.lock` to it, so
/// that the patches of the Charon build do not change the workspace one.
const CHARON_LOCK: &str = "lusterna/charon/Cargo.lock";

/// The `lakefile.toml` of the Lean project, with `{rev}` standing for the
/// Aeneas git revision. `extract.sh` makes Aeneas write the generated modules
/// to `Extraction/`.
const LAKEFILE_TEMPLATE: &str = include_str!("res/lakefile.toml.template");

/// The `lakefile.toml` of the Lean project of a Solana crate, with
/// `{aeneas_root}` and `{solanalib_root}` standing for the Aeneas and
/// Solanalib checkouts.
const LAKEFILE_SOLANA_TEMPLATE: &str = include_str!("res/lakefile-solana.toml.template");

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
    #[error("`{0}` exists from an earlier run. Delete it and run the stage again")]
    AlreadyExists(PathBuf),
    #[error("`{0}` is not on `PATH`")]
    NotInPath(&'static str),
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error(transparent)]
    Lake(#[from] LakeError),
    #[error(transparent)]
    Git(#[from] GitError),
    #[error("cannot parse `{path}`: {source}")]
    ParseManifest {
        path: PathBuf,
        #[source]
        source: toml_edit::TomlError,
    },
}

/// The paths that setup created in the crate root.
#[derive(Debug, Clone)]
pub(super) struct SetupOutcome {
    /// Absolute path to `lusterna/extract.sh`
    pub extraction_script: PathBuf,
    /// Absolute path to the lean project root dir `lusterna/lean`
    pub lean_root: PathBuf,
    /// Hash of the commit with the files that setup created and changed
    pub setup_commit: String,
}

/// Sets up the crate for the agent:
///
/// - checks that `charon` and `aeneas` are on `PATH`
/// - checks that `lusterna/extract.sh` and `lusterna/lean` do not exist in the
///   crate root
/// - checks that `input.workspace_root` is the root of a Cargo workspace
/// - checks that the crate root is a crate in that workspace
/// - builds the crate
/// - adds the `lusterna` feature to the crate
/// - creates the extraction script
/// - creates the lean project
/// - commits all changes in the git repository of the crate
pub(super) fn run_setup(input: &TranslateInput) -> Result<SetupOutcome, TranslateSetupErr> {
    let path = env::var_os("PATH").unwrap_or_default();
    for tool in ["charon", "aeneas"] {
        if !env::split_paths(&path).any(|dir| dir.join(tool).is_file()) {
            return Err(TranslateSetupErr::NotInPath(tool));
        }
    }

    let workspace_root = &input.workspace_root;
    let crate_root = input.crate_root();
    for path in [
        crate_root.join("lusterna").join("extract.sh"),
        crate_root.join("lusterna").join("lean"),
    ] {
        if path.exists() {
            return Err(TranslateSetupErr::AlreadyExists(path));
        }
    }

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

    translate_info!("setup: checking if the project builds");
    cargo::build(&crate_manifest)?;
    translate_info!("setup: it builds");

    cargo::add_feature(&crate_manifest, "lusterna")?;
    translate_info!(
        "setup: added 'lusterna' feature to Cargo.toml ({})",
        crate_manifest.display()
    );

    // If we are on solana, add "--sysroot default" to prevent charon from crashing
    let charon_args = match input.platform {
        Platform::Generic => vec![],
        Platform::Solana { .. } => vec!["--sysroot".to_owned(), "default".to_owned()],
    };
    // On Solana, the Charon build uses the `lusterna-stubs` crates in place of
    // the crates of the same name, with its own `Cargo.lock`
    let (cargo_args, charon_lock) = match &input.platform {
        Platform::Generic => (vec![], None),
        Platform::Solana { replacements, .. } => {
            let mut args = replacements::patch_args(replacements);
            args.extend([
                "-Zlockfile-path".to_owned(),
                "--config".to_owned(),
                format!("resolver.lockfile-path=\"{CHARON_LOCK}\""),
            ]);
            let workspace_lock = ws_manifest.with_file_name("Cargo.lock");
            (args, Some(relative_path(crate_root, &workspace_lock)?))
        }
    };
    let extraction_script = create_extraction_script(
        crate_root,
        &input.entrypoints,
        &input.opaque,
        &charon_args,
        &cargo_args,
        charon_lock.as_deref(),
    )?;

    translate_info!(
        "setup: created extraction script at {}",
        extraction_script.display()
    );

    translate_info!("setup: initializing lake project (may take a minute)");
    let lakefile = match &input.platform {
        Platform::Generic => make_lakefile(&input.aeneas_rev),
        Platform::Solana { solanalib_root, .. } => {
            make_solana_lakefile(&input.aeneas_root, solanalib_root)
        }
    };
    let lean_root = init_lake_project(crate_root, &lakefile, &input.lean_toolchain)?;
    if let Platform::Solana { replacements, .. } = &input.platform {
        let manifest: toml_edit::DocumentMut =
            fs::read_to_string(&ws_manifest)?
                .parse()
                .map_err(|source| TranslateSetupErr::ParseManifest {
                    path: ws_manifest.clone(),
                    source,
                })?;
        let extraction = lean_root.join("Extraction");
        fs::create_dir_all(&extraction)?;
        fs::write(
            extraction.join("Lusterna.lean"),
            replacements::lean_module(replacements, &manifest),
        )?;
        let mut gitignore = fs::read_to_string(crate_root.join("lusterna/.gitignore"))?;
        gitignore.push_str("/charon/\n");
        fs::write(crate_root.join("lusterna/.gitignore"), gitignore)?;
    }
    translate_info!("setup: initialized lake project at {}", lean_root.display());

    let setup_commit = git::commit_all(crate_root, "lusterna: set up translation")?;
    translate_info!("setup: committed setup in {setup_commit}");

    Ok(SetupOutcome {
        extraction_script,
        lean_root,
        setup_commit,
    })
}

/// Returns the content of `lusterna/extract.sh` for the given Charon
/// `--start-from` and `--opaque` patterns and extra `charon` and `cargo`
/// arguments. If `charon_lock` is given, the script first copies that
/// `Cargo.lock`, relative to the crate root, to `CHARON_LOCK`.
pub(super) fn make_extraction_script(
    entrypoints: &[String],
    opaque: &[String],
    charon_args: &[String],
    cargo_args: &[String],
    charon_lock: Option<&str>,
) -> String {
    let quote_one = |s: &str| format!("'{}'", s.replace('\'', r"'\''"));
    let quote = |patterns: &[String]| {
        patterns
            .iter()
            .map(|p| quote_one(p))
            .collect::<Vec<_>>()
            .join(" ")
    };
    let charon_lock = charon_lock.map_or(String::new(), |lock| {
        format!(
            "mkdir -p {} && cp {} {}",
            quote_one(Path::new(CHARON_LOCK).parent().unwrap().to_str().unwrap()),
            quote_one(lock),
            quote_one(CHARON_LOCK)
        )
    });
    EXTRACT_SH_TEMPLATE
        .replace("{entrypoints}", &quote(entrypoints))
        .replace("{opaque}", &quote(opaque))
        .replace("{charon_args}", &quote(charon_args))
        .replace("{cargo_args}", &quote(cargo_args))
        .replace("{charon_lock}", &charon_lock)
}

/// Returns `path` relative to the directory `dir`. Both must exist.
fn relative_path(dir: &Path, path: &Path) -> Result<String, io::Error> {
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
    Ok(relative.display().to_string())
}

/// Returns the content of the Lean project's `lakefile.toml` for the given
/// Aeneas git revision.
fn make_lakefile(aeneas_rev: &str) -> String {
    LAKEFILE_TEMPLATE.replace("{rev}", aeneas_rev)
}

/// Returns the content of the Lean project's `lakefile.toml` for a Solana
/// crate, which requires Aeneas and SolanalibAeneas from the given checkouts.
fn make_solana_lakefile(aeneas_root: &Path, solanalib_root: &Path) -> String {
    LAKEFILE_SOLANA_TEMPLATE
        .replace("{aeneas_root}", &aeneas_root.display().to_string())
        .replace("{solanalib_root}", &solanalib_root.display().to_string())
}

/// Creates the lean project in `lusterna/lean` of the crate root:
/// - writes `lakefile` as `lakefile.toml`
/// - writes the `lean-toolchain` and calls `lake update`
/// - writes `lusterna/.gitignore`, which ignores the project's `.lake` and
///   `lusterna/extraction.llbc`
///
/// Returns the path to the lean project root.
/// Returns an error if failed.
fn init_lake_project(
    crate_root: &Path,
    lakefile: &str,
    lean_toolchain: &str,
) -> Result<PathBuf, TranslateSetupErr> {
    let lean_root = crate_root.join("lusterna").join("lean");
    fs::create_dir_all(&lean_root)?;
    fs::write(
        crate_root.join("lusterna").join(".gitignore"),
        "/lean/.lake/\n/extraction.llbc\n",
    )?;
    fs::write(lean_root.join("lakefile.toml"), lakefile)?;
    fs::write(
        lean_root.join("lean-toolchain"),
        format!("{}\n", lean_toolchain.trim()),
    )?;

    lean::lake_update(&lean_root)?;

    Ok(lean_root)
}

/// Creates the initial extraction script.
/// Returns the path to the script.
/// Returns an error if creation failed
fn create_extraction_script(
    crate_root: &Path,
    entrypoints: &[String],
    opaque: &[String],
    charon_args: &[String],
    cargo_args: &[String],
    charon_lock: Option<&str>,
) -> Result<PathBuf, io::Error> {
    let path = crate_root.join("lusterna").join("extract.sh");

    fs::create_dir_all(path.parent().unwrap())?;
    fs::write(
        &path,
        make_extraction_script(entrypoints, opaque, charon_args, cargo_args, charon_lock),
    )?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755))?;
    Ok(path)
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

        let script = make_extraction_script(&entrypoints, &opaque, &[], &[], None);

        assert_eq!(bash_array(&script, "ENTRYPOINTS"), entrypoints);
        assert_eq!(bash_array(&script, "OPAQUE"), opaque);
    }

    #[test]
    fn extract_sh_allows_empty_patterns() {
        let script = make_extraction_script(&[], &[], &[], &[], None);

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

    #[test]
    fn solana_lakefile_requires_local_checkouts() {
        let doc: DocumentMut = make_solana_lakefile(Path::new("/a"), Path::new("/s"))
            .parse()
            .unwrap();

        assert_eq!(
            doc["packagesDir"].as_str(),
            Some("/a/backends/lean/.lake/packages")
        );
        let aeneas = &doc["require"][0];
        assert_eq!(aeneas["name"].as_str(), Some("aeneas"));
        assert_eq!(aeneas["path"].as_str(), Some("/a/backends/lean"));
        let solanalib = &doc["require"][1];
        assert_eq!(solanalib["name"].as_str(), Some("solanalibAeneas"));
        assert_eq!(solanalib["path"].as_str(), Some("/s/aeneas"));
    }

    #[test]
    fn extract_sh_copies_charon_lock_and_passes_cargo_args() {
        let cargo_args = vec!["--config".to_owned(), "a.b=\"c d\"".to_owned()];
        let script = make_extraction_script(&[], &[], &[], &cargo_args, Some("../../Cargo.lock"));

        assert_eq!(bash_array(&script, "CARGO_ARGS"), cargo_args);
        assert!(script.contains(
            "mkdir -p 'lusterna/charon' && cp '../../Cargo.lock' 'lusterna/charon/Cargo.lock'"
        ));
    }

    #[test]
    fn relative_path_goes_up_to_the_common_dir() {
        let dir = tempfile::tempdir().unwrap();
        let crate_root = dir.path().join("programs/klend");
        fs::create_dir_all(&crate_root).unwrap();
        fs::write(dir.path().join("Cargo.lock"), "").unwrap();

        let relative = relative_path(&crate_root, &dir.path().join("Cargo.lock")).unwrap();

        assert_eq!(relative, "../../Cargo.lock");
    }
}
