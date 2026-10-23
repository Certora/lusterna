use std::time::{Duration, Instant};

pub mod cargo;
pub mod git;
pub(crate) mod lean;

/// Formats the time elapsed since `t`, rounded down to whole seconds.
pub fn format_elapsed(t: Instant) -> humantime::FormattedDuration {
    humantime::format_duration(Duration::from_secs(t.elapsed().as_secs()))
}
