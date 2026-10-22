use std::{
    env, fs,
    path::{self, Path, PathBuf},
};

use anyhow::{Context, anyhow};
use clap::{Parser, Subcommand};
use lusterna::{
    Config,
    stage::{
        self,
        translate::{TranslateInput, TranslateStage},
    },
    utils::git,
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
    Translate {
        /// Workspace of the Rust project
        workspace_root: PathBuf,
        /// Crate inside the workspace, if the workspace has more than one crate
        #[arg(long)]
        crate_root: Option<PathBuf>,
        /// Charon `--start-from` pattern
        #[arg(long = "entrypoint", required = true)]
        entrypoints: Vec<String>,
        /// Charon `--opaque` pattern
        #[arg(long)]
        opaque: Vec<String>,
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
    },
}

fn main() -> anyhow::Result<()> {
    env_logger::init();
    let cli = Cli::parse();
    match cli.command {
        Command::Translate {
            workspace_root,
            crate_root,
            entrypoints,
            opaque,
            aeneas_root,
            source_branch,
            target_branch,
            worktree,
        } => translate(
            &cli.config,
            workspace_root,
            crate_root,
            entrypoints,
            opaque,
            aeneas_root,
            source_branch,
            target_branch,
            worktree,
        ),
    }
}

fn translate(
    config: &Config,
    workspace_root: PathBuf,
    crate_root: Option<PathBuf>,
    entrypoints: Vec<String>,
    opaque: Vec<String>,
    aeneas_root: PathBuf,
    source_branch: Option<String>,
    target_branch: String,
    worktree: Option<PathBuf>,
) -> anyhow::Result<()> {
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
    let toolchain = aeneas_root.join("backends/lean/lean-toolchain");

    let input = TranslateInput {
        workspace_root: in_worktree(&workspace_root)?,
        crate_root: crate_root.as_deref().map(in_worktree).transpose()?,
        entrypoints,
        opaque,
        aeneas_rev: git::head_rev(&aeneas_root)?,
        lean_toolchain: fs::read_to_string(&toolchain)
            .with_context(|| format!("cannot read `{}`", toolchain.display()))?,
    };
    let output = stage::run(TranslateStage::new(input), config)?;
    git::commit_all(&worktree, "lusterna: translate to Lean with Aeneas")?;
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}
