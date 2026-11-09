use std::{fs, path::PathBuf};

use anyhow::Context;
use clap::{Args, Parser, Subcommand};
use lusterna::{config::Config, pipeline::Pipeline, spec::Spec};

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
    /// Runs lusterna on the crate with the spec at `SPEC`
    Run(RunArgs),
}

#[derive(Args)]
struct RunArgs {
    /// The spec of the run. Defaults to `lusterna.json` in the workspace root.
    spec: Option<PathBuf>,
}

fn main() -> anyhow::Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let cli = Cli::parse();
    let Command::Run(args) = cli.command;
    let config = cli.config;

    let spec_path = match args.spec {
        Some(spec) => spec,
        None => config.workspace_root()?.join("lusterna.json"),
    };
    let spec_path = spec_path
        .canonicalize()
        .with_context(|| format!("cannot find the spec `{}`", spec_path.display()))?;
    let spec: Spec = serde_json::from_str(&fs::read_to_string(&spec_path)?)
        .with_context(|| format!("cannot parse the spec `{}`", spec_path.display()))?;

    Pipeline::new(spec, spec_path, config).run()?;
    Ok(())
}
