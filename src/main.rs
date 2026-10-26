use std::{
    env, fs,
    path::{self, Path, PathBuf},
    process,
    time::Instant,
};

use anyhow::{Context, anyhow, bail};
use clap::{Args, Parser, Subcommand};
use log::{info, warn};
use lusterna::{
    config::{Config, Platform},
    stage::{
        self,
        pre_translate::{PreTranslateInput, PreTranslateStage},
        translate::{DEFAULT_OPAQUE, SOLANA_OPAQUE, TranslateInput, TranslateStage},
    },
    utils::{
        cargo::{self, CargoError},
        format_elapsed, git,
    },
};

#[derive(Parser)]
#[command(version, about)]
struct Cli {
    #[command(flatten)]
    config: Config,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Translates a Rust crate to Lean with Aeneas
    Translate(TranslateArgs),
}

#[derive(Args)]
struct TranslateArgs {
    /// Workspace of the Rust project
    workspace_root: PathBuf,
    /// Crate inside the workspace, if the workspace has more than one crate
    #[arg(long)]
    crate_root: Option<PathBuf>,
    /// Charon `--start-from` pattern
    #[arg(long = "entrypoint", required = true)]
    entrypoints: Vec<String>,
    /// Charon `--opaque` pattern. Replaces the tested default patterns.
    #[arg(long, conflicts_with = "add_opaque")]
    opaque: Option<Vec<String>>,
    /// Charon `--opaque` pattern to add to the tested default patterns.
    #[arg(long)]
    add_opaque: Vec<String>,
    /// Aeneas checkout
    #[arg(long, env = "AENEAS_ROOT")]
    aeneas_root: PathBuf,
    /// Branch to start from [default: the checked out branch]
    #[arg(long)]
    source_branch: Option<String>,
    /// New branch to do the work on. Must not exist yet.
    #[arg(long)]
    target_branch: String,
    /// Where to create the worktree of the target branch
    /// [default: ~/.lusterna/<project>_<source branch>_<target branch>]
    #[arg(long)]
    worktree: Option<PathBuf>,
    /// Stop after the pre-translate stage
    #[arg(long, help_heading = "Dev options")]
    stop_after_pre_translate: bool,
}

fn main() -> anyhow::Result<()> {
    env_logger::init();
    let cli = Cli::parse();
    match cli.command {
        Command::Translate(args) => translate(&cli.config, args),
    }
}

fn translate(config: &Config, args: TranslateArgs) -> anyhow::Result<()> {
    let TranslateArgs {
        workspace_root,
        crate_root,
        entrypoints,
        opaque,
        add_opaque,
        aeneas_root,
        source_branch,
        target_branch,
        worktree,
        stop_after_pre_translate,
    } = args;
    let canonicalize =
        |p: &Path| fs::canonicalize(p).with_context(|| format!("cannot resolve `{}`", p.display()));
    let workspace_root = canonicalize(&workspace_root)?;
    let repo_root = git::toplevel(&workspace_root)?;
    let source_branch = match source_branch {
        Some(b) => b,
        None => git::current_branch(&repo_root)?,
    };
    let worktree = match worktree {
        Some(w) => {
            path::absolute(&w).with_context(|| format!("cannot resolve `{}`", w.display()))?
        }
        None => {
            let project = repo_root
                .file_name()
                .ok_or_else(|| anyhow!("repository root `{}` has no name", repo_root.display()))?
                .to_string_lossy();
            PathBuf::from(env::var("HOME").context("`HOME` is not set")?)
                .join(".lusterna")
                .join(format!("{project}_{source_branch}_{target_branch}").replace('/', "-"))
        }
    };
    info!(
        "Checking out worktree for branch {} at {}",
        target_branch,
        worktree.display()
    );
    git::add_worktree(&repo_root, &target_branch, &worktree, &source_branch)?;
    let in_worktree = |p: &Path| -> anyhow::Result<PathBuf> {
        let p = canonicalize(p)?;
        let relative = p.strip_prefix(&repo_root).with_context(|| {
            format!(
                "`{}` is not inside the repository `{}`",
                p.display(),
                repo_root.display()
            )
        })?;
        Ok(worktree.join(relative))
    };

    // get the aeneas revision and the toolchain it uses
    let aeneas_rev = git::head_rev(&aeneas_root)?;
    let output = process::Command::new("aeneas")
        .arg("-version")
        .output()
        .context("cannot run `aeneas -version`")?;
    let version = String::from_utf8_lossy(&output.stdout);
    let built_rev = version.trim().strip_prefix("aeneas ").unwrap_or_default();
    let (built_rev, dirty) = match built_rev.strip_suffix("-dirty") {
        Some(rev) => (rev, true),
        None => (built_rev, false),
    };
    if built_rev.is_empty() || !aeneas_rev.starts_with(built_rev) {
        bail!(
            "`aeneas` on `PATH` reports version `{}`, but `{}` is at {}. Rebuild Aeneas or check out the matching commit",
            version.trim(),
            aeneas_root.display(),
            aeneas_rev
        );
    }
    if dirty {
        warn!("`aeneas` on `PATH` was built from a checkout with uncommitted changes");
    }
    let toolchain_file = aeneas_root.join("backends/lean/lean-toolchain");
    let lean_toolchain = fs::read_to_string(&toolchain_file)
        .with_context(|| format!("cannot read `{}`", toolchain_file.display()))?
        .trim()
        .to_string();

    info!(
        "Found Aeneas revision {}, using same lean toolchain: {}",
        aeneas_rev, lean_toolchain
    );
    let workspace_root = in_worktree(&workspace_root)?;
    let crate_root = crate_root.as_deref().map(in_worktree).transpose()?;
    let manifest = crate_root
        .as_deref()
        .unwrap_or(&workspace_root)
        .join("Cargo.toml");
    match cargo::build(&manifest) {
        Ok(()) => info!("Crate builds, skipping pre-translate phase"),
        Err(CargoError::Build { stderr, .. }) => {
            info!("Crate does not build, starting pre-translate phase");
            let t = Instant::now();
            let input = PreTranslateInput {
                workspace_root: workspace_root.clone(),
                manifest: manifest.clone(),
                build_stderr: stderr,
            };
            stage::run(PreTranslateStage::new(input), config)?;
            log::info!("Finished pre-translate in {}", format_elapsed(t));

            let sha = git::commit_all(&worktree, "lusterna: make the crate build on the host")?;
            log::info!("Committed pre-translate in {}", sha);
        }
        Err(e) => return Err(e.into()),
    }
    if stop_after_pre_translate {
        return Ok(());
    }

    let platform = if cargo::lock_has_package(&manifest, "solana-program")? {
        Platform::Solana
    } else {
        Platform::Generic
    };
    let mut opaque = match opaque {
        Some(opaque) => opaque,
        None => {
            let mut opaque: Vec<String> = DEFAULT_OPAQUE.lines().map(str::to_owned).collect();
            // Add addition opaque patterns if we are running on a solana program
            if let Platform::Solana = platform {
                opaque.extend(SOLANA_OPAQUE.lines().map(str::to_owned));
            }
            opaque
        }
    };
    opaque.extend(add_opaque);

    info!("Starting translation phase");

    let t = Instant::now();
    let input = TranslateInput {
        workspace_root,
        crate_root,
        entrypoints,
        opaque,
        aeneas_root: canonicalize(&aeneas_root)?,
        aeneas_rev,
        lean_toolchain,
        platform,
    };
    let translate_stage = TranslateStage::new(input);
    let output = stage::run(translate_stage, config)?;
    log::info!("Finished translation in {}", format_elapsed(t));

    let sha = git::commit_all(&worktree, "lusterna: translate to Lean with Aeneas")?;
    log::info!("Committed translation in {}", sha);

    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}
