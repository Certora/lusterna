use std::{
    env, fs,
    io::{self, IsTerminal},
    path::PathBuf,
};

use anyhow::{Context, bail};
use clap::{Args, Parser, Subcommand, ValueEnum};
use lusterna::{
    config::{AENEAS_ROOT, Config},
    pipeline::{Pipeline, PipelineStep},
    precheck,
    spec::Spec,
};

#[derive(Parser)]
#[command(version, about)]
struct Cli {
    #[command(flatten)]
    config: Config,
    /// Run without asking for confirmation when `claude` is not logged in
    /// with a subscription, or when `reset` deletes work
    #[arg(long, short, global = true)]
    yes: bool,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Runs lusterna on the crate with the spec at `SPEC`
    Run(RunArgs),
    /// Continues the unfinished run of the crate
    Continue,
    /// Resets the run of the crate to the start of `STAGE`, so that
    /// `continue` runs it again
    Reset(ResetArgs),
}

#[derive(Args)]
struct ResetArgs {
    /// The stage to run again: `pre-translate`, `translate`, `formalize` or
    /// `prove`. Its work stays in the work tree as uncommitted changes, and
    /// the work of the later stages is deleted.
    #[arg(value_enum)]
    stage: PipelineStep,
    /// Delete the work of `STAGE` too
    #[arg(long)]
    full: bool,
    /// Reset even if this deletes commits that Lusterna did not make or
    /// uncommitted changes. Asks for confirmation.
    #[arg(long)]
    force: bool,
}

#[derive(Args)]
struct RunArgs {
    /// The spec of the run. Defaults to `lusterna.json` in the workspace root.
    spec: Option<PathBuf>,
}

fn main() -> anyhow::Result<()> {
    // Put the `aeneas` and `charon` of `AENEAS_ROOT` first on `PATH`
    let mut path = vec![
        PathBuf::from(AENEAS_ROOT).join("bin"),
        PathBuf::from(AENEAS_ROOT).join("charon/bin"),
    ];
    path.extend(env::split_paths(&env::var_os("PATH").unwrap_or_default()));
    // SAFETY: no other thread runs yet
    unsafe { env::set_var("PATH", env::join_paths(path)?) };

    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let cli = Cli::parse();
    let config = cli.config;

    if !matches!(cli.command, Command::Reset(_))
        && !Config::claude_uses_subscription()?
        && !confirm(
            "Warning: `claude` is not logged in with a subscription. This run is billed per token, which costs much more.",
            cli.yes,
        )?
    {
        return Ok(());
    }

    match cli.command {
        Command::Run(args) => {
            let spec_path = match args.spec {
                Some(spec) => spec,
                None => config.workspace_root()?.join("lusterna.json"),
            };
            let spec_path = spec_path
                .canonicalize()
                .with_context(|| format!("cannot find the spec `{}`", spec_path.display()))?;
            let spec: Spec = serde_json::from_str(&fs::read_to_string(&spec_path)?)
                .with_context(|| format!("cannot parse the spec `{}`", spec_path.display()))?;
            precheck::check_dependencies(&config)?;
            precheck::check_clean_worktree(&config, &spec_path)?;
            Pipeline::new(spec, spec_path, config)?.run()?
        }
        Command::Continue => {
            precheck::check_dependencies(&config)?;
            Pipeline::load(config)?.resume()?
        }
        Command::Reset(args) => {
            if matches!(
                args.stage,
                PipelineStep::Infer | PipelineStep::WriteSpec | PipelineStep::Report
            ) {
                let step = args.stage.to_possible_value().expect("no step is skipped");
                bail!(
                    "cannot reset to `{}`; start a new run with `lusterna run`",
                    step.get_name()
                );
            }
            let repo_root = config.repo_root()?;
            let pipeline = Pipeline::load(config)?;
            let lost = precheck::lost_by_reset(&repo_root, pipeline.steps(), args.stage)?;
            if !lost.commits.is_empty() || !lost.files.is_empty() {
                let mut text = String::from("The reset deletes:");
                for commit in &lost.commits {
                    text.push_str(&format!("\n- the commit {commit}"));
                }
                for file in &lost.files {
                    let file = file.display();
                    text.push_str(&format!("\n- the uncommitted changes to `{file}`"));
                }
                if !args.force {
                    bail!("{text}\nPass `--force` to reset anyway.");
                }
                if !confirm(&text, cli.yes)? {
                    return Ok(());
                }
            }
            if !confirm("This deletes work and cannot be undone.", cli.yes)? {
                return Ok(());
            }
            pipeline.reset(args.stage, args.full)?
        }
    }
    Ok(())
}

fn confirm(text: &str, yes: bool) -> anyhow::Result<bool> {
    eprintln!("{text}");
    if yes {
        return Ok(true);
    }
    if !io::stdin().is_terminal() {
        bail!("stdin is not a terminal, pass `--yes` to run without confirmation");
    }
    eprint!("Do you want to continue [y/n] ");
    let mut answer = String::new();
    io::stdin().read_line(&mut answer)?;
    Ok(answer.trim() == "y")
}
