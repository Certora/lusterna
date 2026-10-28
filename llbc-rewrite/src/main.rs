use std::{
    fs::{self, File},
    io::{self, BufWriter, Write},
    path::PathBuf,
    process::ExitCode,
};

use clap::Parser;

/// The exit code on an error. The translate stage of `lusterna` ends the run
/// when `extract.sh` exits with it.
const ERROR_EXIT_CODE: u8 = 70;

#[derive(Parser)]
#[command(version, about)]
struct Cli {
    /// LLBC file to rewrite
    llbc: PathBuf,
    /// File to write the rewritten LLBC to. Writes to stdout if not given.
    #[arg(long)]
    out: Option<PathBuf>,
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("Error: {e:?}");
            ExitCode::from(ERROR_EXIT_CODE)
        }
    }
}

fn run() -> anyhow::Result<()> {
    let cli = Cli::parse();
    let mut writer: Box<dyn Write> = match &cli.out {
        Some(path) => Box::new(BufWriter::new(File::create(path)?)),
        None => Box::new(BufWriter::new(io::stdout().lock())),
    };
    match llbc_rewrite::run(&cli.llbc)? {
        Some(crate_data) => serde_json::to_writer(&mut writer, &crate_data)?,
        None => writer.write_all(&fs::read(&cli.llbc)?)?,
    }
    writer.flush()?;
    Ok(())
}
