use clap::{Args, Parser, Subcommand};
use lusterna::{
    config::{Config, Platform},
    pipeline::Pipeline,
    stage::translate::{DEFAULT_OPAQUE, solana_opaque},
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
    /// Charon `--start-from` pattern
    #[arg(long = "entrypoint", required = true)]
    entrypoints: Vec<String>,
    /// Charon `--opaque` pattern. Replaces the tested default patterns.
    #[arg(long, conflicts_with = "add_opaque")]
    opaque: Option<Vec<String>>,
    /// Charon `--opaque` pattern to add to the tested default patterns.
    #[arg(long)]
    add_opaque: Vec<String>,
}

fn main() -> anyhow::Result<()> {
    env_logger::init();
    let cli = Cli::parse();
    let pipeline = match cli.command {
        Command::Translate(args) => translate(cli.config, args),
    }?;

    pipeline.run()?;
    Ok(())
}

fn translate(config: Config, args: TranslateArgs) -> anyhow::Result<Pipeline> {
    let TranslateArgs {
        entrypoints,
        opaque,
        add_opaque,
    } = args;

    config.ensure_aeneas_version_match()?;

    let mut opaque = match opaque {
        Some(opaque) => {
            log::warn!(
                "Overwriting opaque setting can lead to unexpected outcomes. Prefer using --add-opaque"
            );
            opaque
        }
        None => {
            let mut opaque: Vec<String> = DEFAULT_OPAQUE.lines().map(str::to_owned).collect();
            // Add addition opaque patterns if we are running on a solana
            // program
            if let Platform::Solana { .. } = config.get_platform()? {
                opaque.extend(solana_opaque());
            }
            opaque
        }
    };
    opaque.extend(add_opaque);

    let pipeline = Pipeline::translate(entrypoints, opaque, config);

    Ok(pipeline)
}
