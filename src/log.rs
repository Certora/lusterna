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
