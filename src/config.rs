use std::path::PathBuf;

use crate::agent::Effort;

pub const DEFAULT_STAGE_MAX_ATTEMPTS: usize = 5;
pub const DEFAULT_MAX_BUDGED_USD: usize = 50;

pub const DEFAULT_MODEL: &str = "opus";
pub const DEFAULT_EFFORT: Effort = Effort::High;

#[derive(clap::Args)]
pub struct Config {
    /// Maximum attempts per stage
    #[arg(long, global = true, default_value_t = DEFAULT_STAGE_MAX_ATTEMPTS)]
    pub(crate) stage_max_attempts: usize,
    /// Maximum budget per stage in USD
    #[arg(long, global = true, default_value_t = DEFAULT_MAX_BUDGED_USD)]
    pub(crate) stage_max_budget: usize,

    // Claude settings
    /// Claude model
    #[arg(long, global = true, default_value_t = DEFAULT_MODEL.to_string())]
    pub(crate) model: String,
    /// Claude effort level
    #[arg(long, global = true, value_enum, default_value_t = DEFAULT_EFFORT)]
    pub(crate) effort: Effort,
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

/// The platform that the crate is written for. It selects the default opaque
/// patterns, the Lean project and the skills that the translate agent gets.
#[derive(Clone)]
pub enum Platform {
    /// A crate with no specific platform. The default opaque patterns are
    /// the general ones, and the translate agent gets no platform skill.
    Generic,
    /// A Solana program, that is, a crate whose workspace `Cargo.lock` has
    /// `solana-program`. The default opaque patterns also include the Solana
    /// ones, the Lean project requires `SolanalibAeneas`, and the translate
    /// agent gets the skill `solana-translate`.
    Solana {
        /// Absolute path to the Solanalib checkout.
        solanalib_root: PathBuf,
    },
}
