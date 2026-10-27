use std::{
    env, fs, io,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};

use crate::{
    config::Platform,
    stage::translate::{ReplacementsError, TranslateInput, replacements, translate_info},
    utils::{
        cargo::{self, CargoError},
        git::{self, GitError},
        lean::{self, LakeError},
        relative_path,
    },
};

/// The extraction script `lusterna/extract.sh`, with `{entrypoints}`,
/// `{opaque}`, `{charon_args}` and `{cargo_args}` standing for the
/// bash-quoted patterns and the extra `charon` and `cargo` arguments, and
/// `{charon_lock}` standing for the line that creates the `Cargo.lock` of the
/// Charon build.
const EXTRACT_SH_TEMPLATE: &str = include_str!("res/extract.sh.template");

/// The `Cargo.lock` that the Charon build uses, relative to the crate root.
/// `extract.sh` copies the workspace `Cargo.lock` to it, so that changes for
/// the Charon build do not change the workspace one.
const CHARON_LOCK: &str = "lusterna/charon/Cargo.lock";

/// The extra arguments and the `Cargo.lock` of the Charon build in
/// `extract.sh`.
#[derive(Default)]
pub(super) struct CharonBuild {
    /// Extra `charon` arguments.
    charon_args: Vec<String>,
    /// Extra `cargo` arguments.
    cargo_args: Vec<String>,
    /// The `Cargo.lock`, relative to the crate root, that `extract.sh` copies
    /// to `CHARON_LOCK` before the build.
    lock: PathBuf,
}

impl CharonBuild {
    /// The Charon build of the crate at `crate_root` in the workspace of
    /// `ws_manifest`. It builds with `CHARON_LOCK`. On Solana, it also passes
    /// `--sysroot default` to Charon, and it builds with the `lusterna-stubs`
    /// crates in place of the crates of the same name.
    fn new(platform: &Platform, crate_root: &Path, ws_manifest: &Path) -> Result<Self, io::Error> {
        let mut build = Self {
            charon_args: Vec::new(),
            cargo_args: vec![
                "-Zlockfile-path".to_owned(),
                "--config".to_owned(),
                format!("resolver.lockfile-path=\"{CHARON_LOCK}\""),
            ],
            lock: relative_path(crate_root, &ws_manifest.with_file_name("Cargo.lock"))?,
        };
        if let Platform::Solana { .. } = platform {
            build.charon_args = vec!["--sysroot".to_owned(), "default".to_owned()];
            build.cargo_args.extend(replacements::patch_args());
        }
        Ok(build)
    }
}

/// The `lakefile.toml` of the Lean project, with `{rev}` standing for the
/// Aeneas git revision. `extract.sh` makes Aeneas write the generated modules
/// to `Extraction/`.
const LAKEFILE_TEMPLATE: &str = include_str!("res/lakefile.toml.template");

/// The `lakefile.toml` of the Lean project of a Solana crate, with
/// `{aeneas_root}`, `{solanalib_root}` and `{lusterna_lean_root}` standing for
/// the Aeneas and Solanalib checkouts and `LUSTERNA_LEAN`.
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
    #[error(transparent)]
    Replacements(#[from] ReplacementsError),
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

    let charon_build = CharonBuild::new(&input.platform, crate_root, &ws_manifest)?;
    let extraction_script =
        create_extraction_script(crate_root, &input.entrypoints, &input.opaque, &charon_build)?;

    translate_info!(
        "setup: created extraction script at {}",
        extraction_script.display()
    );

    translate_info!("setup: initializing lake project (may take a minute)");
    let lakefile = match &input.platform {
        Platform::Generic => make_lakefile(&input.aeneas_rev),
        Platform::Solana { solanalib_root } => {
            make_solana_lakefile(&input.aeneas_root, solanalib_root)
        }
    };
    let lean_root = init_lake_project(crate_root, &lakefile, &input.lean_toolchain)?;
    if let Platform::Solana { .. } = input.platform {
        let extraction = lean_root.join("Extraction");
        fs::create_dir_all(&extraction)?;
        fs::write(
            extraction.join("Lusterna.lean"),
            replacements::lean_module(&replacements::all(), &ws_manifest)?,
        )?;
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
/// `--start-from` and `--opaque` patterns and Charon build.
pub(super) fn make_extraction_script(
    entrypoints: &[String],
    opaque: &[String],
    build: &CharonBuild,
) -> String {
    let quote_one = |s: &str| format!("'{}'", s.replace('\'', r"'\''"));
    let quote = |patterns: &[String]| {
        patterns
            .iter()
            .map(|p| quote_one(p))
            .collect::<Vec<_>>()
            .join(" ")
    };
    let charon_lock = format!(
        "mkdir -p {} && cp {} {}",
        quote_one(Path::new(CHARON_LOCK).parent().unwrap().to_str().unwrap()),
        quote_one(&build.lock.display().to_string()),
        quote_one(CHARON_LOCK)
    );
    EXTRACT_SH_TEMPLATE
        .replace("{entrypoints}", &quote(entrypoints))
        .replace("{opaque}", &quote(opaque))
        .replace("{charon_args}", &quote(&build.charon_args))
        .replace("{cargo_args}", &quote(&build.cargo_args))
        .replace("{charon_lock}", &charon_lock)
}

/// Returns the content of the Lean project's `lakefile.toml` for the given
/// Aeneas git revision.
fn make_lakefile(aeneas_rev: &str) -> String {
    LAKEFILE_TEMPLATE.replace("{rev}", aeneas_rev)
}

/// Returns the content of the Lean project's `lakefile.toml` for a Solana
/// crate, which requires Aeneas and Solanalib from the given checkouts, and
/// `LusternaLib`.
fn make_solana_lakefile(aeneas_root: &Path, solanalib_root: &Path) -> String {
    LAKEFILE_SOLANA_TEMPLATE
        .replace("{aeneas_root}", &aeneas_root.display().to_string())
        .replace("{solanalib_root}", &solanalib_root.display().to_string())
        .replace("{lusterna_lean_root}", super::LUSTERNA_LEAN)
}

/// Creates the lean project in `lusterna/lean` of the crate root:
/// - writes `lakefile` as `lakefile.toml`
/// - writes the `lean-toolchain` and calls `lake update`
/// - writes `lusterna/.gitignore`, which ignores the project's `.lake`,
///   `lusterna/extraction.llbc` and the folder of `CHARON_LOCK`
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
        "/lean/.lake/\n/extraction.llbc\n/charon/\n",
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
    build: &CharonBuild,
) -> Result<PathBuf, io::Error> {
    let path = crate_root.join("lusterna").join("extract.sh");

    fs::create_dir_all(path.parent().unwrap())?;
    fs::write(&path, make_extraction_script(entrypoints, opaque, build))?;
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

        let script = make_extraction_script(&entrypoints, &opaque, &CharonBuild::default());

        assert_eq!(bash_array(&script, "ENTRYPOINTS"), entrypoints);
        assert_eq!(bash_array(&script, "OPAQUE"), opaque);
    }

    #[test]
    fn extract_sh_allows_empty_patterns() {
        let script = make_extraction_script(&[], &[], &CharonBuild::default());

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
        assert_eq!(solanalib["name"].as_str(), Some("solanalib"));
        assert_eq!(solanalib["path"].as_str(), Some("/s"));
        let lusterna_lib = &doc["require"][2];
        assert_eq!(lusterna_lib["name"].as_str(), Some("lusternaLib"));
        assert_eq!(
            lusterna_lib["path"].as_str(),
            Some(super::super::LUSTERNA_LEAN)
        );
    }

    #[test]
    fn extract_sh_copies_charon_lock_and_passes_cargo_args() {
        let cargo_args = vec!["--config".to_owned(), "a.b=\"c d\"".to_owned()];
        let build = CharonBuild {
            cargo_args: cargo_args.clone(),
            lock: PathBuf::from("../../Cargo.lock"),
            ..CharonBuild::default()
        };
        let script = make_extraction_script(&[], &[], &build);

        assert_eq!(bash_array(&script, "CARGO_ARGS"), cargo_args);
        assert!(script.contains(
            "mkdir -p 'lusterna/charon' && cp '../../Cargo.lock' 'lusterna/charon/Cargo.lock'"
        ));
    }
}
