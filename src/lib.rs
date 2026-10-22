use crate::agent::Effort;

pub(crate) mod agent;
pub(crate) mod log;
pub mod stage;
pub mod utils;

pub const DEFAULT_STAGE_MAX_ATTEMPTS: usize = 5;
pub const DEFAULT_MAX_BUDGED_USD: usize = 50;

pub const DEFAULT_MODEL: &str = "opus";
pub const DEFAULT_EFFORT: Effort = Effort::High;

#[derive(clap::Args)]
pub struct Config {
    /// Maximum attempts per stage
    #[arg(long, global = true, default_value_t = DEFAULT_STAGE_MAX_ATTEMPTS)]
    stage_max_attempts: usize,
    /// Maximum budget per stage in USD
    #[arg(long, global = true, default_value_t = DEFAULT_MAX_BUDGED_USD)]
    stage_max_budget: usize,

    // Claude settings
    /// Claude model
    #[arg(long, global = true, default_value_t = DEFAULT_MODEL.to_string())]
    model: String,
    /// Claude effort level
    #[arg(long, global = true, value_enum, default_value_t = DEFAULT_EFFORT)]
    effort: Effort,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            stage_max_attempts: DEFAULT_STAGE_MAX_ATTEMPTS,
            stage_max_budget: DEFAULT_MAX_BUDGED_USD,
            model: DEFAULT_MODEL.to_string(),
            effort: DEFAULT_EFFORT,
        }
    }
}
