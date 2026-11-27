/// Logs at `$level` with the name of the `StageName` `$stage` as the log
/// target.
macro_rules! stage_log {
    ($level:ident, $stage:expr, $($arg:tt)+) => {
        ::log::$level!(target: $stage.as_str(), $($arg)+)
    };
}

/// Logs at info level with the name of the `StageName` `$stage` as the log
/// target.
macro_rules! stage_info {
    ($stage:expr, $($arg:tt)+) => {
        $crate::log::stage_log!(info, $stage, $($arg)+)
    };
}

/// Logs at debug level with the name of the `StageName` `$stage` as the log
/// target.
macro_rules! stage_debug {
    ($stage:expr, $($arg:tt)+) => {
        $crate::log::stage_log!(debug, $stage, $($arg)+)
    };
}

pub(crate) use {stage_debug, stage_info, stage_log};

use std::{
    fs::File,
    io::{self, Write},
    path::Path,
    sync::Mutex,
    time::SystemTime,
};

use ::log::{LevelFilter, Log, Metadata, Record};

/// Where `Logger` writes the records of every level.
enum LogFile {
    /// The lines of the records that were logged before `open_file`.
    Pending(Vec<u8>),
    Open(File),
}

static LOG_FILE: Mutex<LogFile> = Mutex::new(LogFile::Pending(Vec::new()));

/// Writes the records that pass the filter of `cli` to the terminal, and the
/// records of every level to `LOG_FILE`.
struct Logger {
    cli: env_logger::Logger,
}

impl Log for Logger {
    fn enabled(&self, _: &Metadata) -> bool {
        true
    }

    fn log(&self, record: &Record) {
        self.cli.log(record);
        let line = format!(
            "[{} {} {}] {}\n",
            humantime::format_rfc3339_seconds(SystemTime::now()),
            record.level(),
            record.target(),
            record.args()
        );
        match &mut *LOG_FILE.lock().unwrap() {
            LogFile::Pending(lines) => lines.extend_from_slice(line.as_bytes()),
            LogFile::Open(file) => {
                let _ = file.write_all(line.as_bytes());
            }
        }
    }

    fn flush(&self) {
        self.cli.flush();
    }
}

/// Installs the logger. The level of the terminal output is the level in
/// `RUST_LOG`, or info if it is not set.
pub fn init() {
    let cli =
        env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).build();
    ::log::set_boxed_logger(Box::new(Logger { cli })).expect("no logger is installed");
    ::log::set_max_level(LevelFilter::Trace);
}

/// Creates the log file `lusterna_<command>_<now>.logs` in `internal_dir` and
/// writes the records of every level to it, starting with the records that
/// were logged before the call.
pub fn open_file(internal_dir: &Path, command: &str) -> io::Result<()> {
    let now = humantime::format_rfc3339_seconds(SystemTime::now())
        .to_string()
        .replace(':', "-");
    let mut file = File::create(internal_dir.join(format!("lusterna_{command}_{now}.logs")))?;
    let mut log_file = LOG_FILE.lock().unwrap();
    if let LogFile::Pending(lines) = &*log_file {
        file.write_all(lines)?;
    }
    *log_file = LogFile::Open(file);
    Ok(())
}
