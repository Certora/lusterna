use std::{
    env, fs,
    io::{self, IsTerminal},
    path::PathBuf,
};

use anyhow::{Context, bail};
use clap::{Args, Parser, Subcommand};
use lusterna::{
    config::{AENEAS_ROOT, Config},
    pipeline::Pipeline,
    precheck,
    spec::Spec,
};

#[derive(Parser)]
#[command(version, about)]
struct Cli {
    #[command(flatten)]
    config: Config,
    /// Run without asking for confirmation when `claude` is not logged in
    /// with a subscription
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

    if !Config::claude_uses_subscription()?
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
