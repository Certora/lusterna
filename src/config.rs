//!
//! ## Config vs. State Input
//!
//! Prefer a Config item over a Stage input whenever the item
//!
//! - is system dependent (e.g. a path on the machine), or
//! - is used by multiple stages
use std::{
    fs::{self, canonicalize},
    path::PathBuf,
};

use anyhow::{Context, bail};
use serde::{Deserialize, Serialize};

use crate::{
    agent::Effort,
    utils::{
        cargo::{self},
        git,
    },
};

pub const DEFAULT_STAGE_MAX_ATTEMPTS: usize = 5;
pub const DEFAULT_MAX_BUDGED_USD: usize = 50;

pub const DEFAULT_MODEL: &str = "opus";
pub const DEFAULT_EFFORT: Effort = Effort::Medium;

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

    /// Aeneas checkout
    #[arg(long, env = "AENEAS_ROOT")]
    pub(crate) aeneas_root: PathBuf,

    // Paths
    /// The path to the Solanalib library. Must be given if the tool targets a
    /// Solana program
    #[arg(long, global = true)]
    pub(crate) solanalib_root: Option<PathBuf>,

    /// Workspace of the Rust project
    pub(crate) workspace_root: PathBuf,
    /// Crate inside the workspace, if the workspace has more than one crate
    #[arg(long)]
    pub(crate) crate_root: Option<PathBuf>,

    /// Branch to start from [default: the checked out branch]
    #[arg(long)]
    pub(crate) source_branch: Option<String>,
    /// The branch to work on. By default it is the currently checked out branch
    #[arg(long)]
    pub(crate) target_branch: Option<String>,
}

impl Config {
    pub fn manifest(&self) -> PathBuf {
        self.crate_root
            .as_deref()
            .unwrap_or(&self.workspace_root)
            .join("Cargo.toml")
    }

    pub fn lean_toolchain(&self) -> anyhow::Result<String> {
        let toolchain_file = self.aeneas_root.join("backends/lean/lean-toolchain");
        Ok(fs::read_to_string(&toolchain_file)
            .with_context(|| format!("cannot read `{}`", toolchain_file.display()))?
            .trim()
            .to_string())
    }

    pub fn get_platform(&self) -> anyhow::Result<Platform> {
        let platform = if cargo::lock_has_package(&self.manifest(), "solana-program")? {
            let solanalib_root = self
                .solanalib_root
                .as_ref()
                .context("the crate is a Solana program, so `--solanalib-root` is required")?;
            let packages = self.aeneas_root.join("backends/lean/.lake/packages");
            if !packages.is_dir() {
                bail!(
                    "`{}` does not exist. Run `lake update` in `{}` once",
                    packages.display(),
                    self.aeneas_root.join("backends/lean").display()
                );
            }
            Platform::Solana {
                solanalib_root: canonicalize(&solanalib_root)?,
            }
        } else {
            Platform::Generic
        };
        Ok(platform)
    }

    pub fn aeneas_rev(&self) -> anyhow::Result<String> {
        Ok(git::head_rev(&self.aeneas_root)?)
    }
}

/// The platform that the crate is written for. It selects the default opaque
/// patterns, the Lean project and the skills that the translate agent gets.
#[derive(Clone, Serialize, Deserialize)]
pub enum Platform {
    /// A crate with no specific platform. The default opaque patterns are
    /// the general ones, and the translate agent gets no platform skill.
    Generic,
    /// A Solana program, that is, a crate whose workspace `Cargo.lock` has
    /// `solana-program`. The default opaque patterns also include the Solana
    /// ones and the items of the replaced crates, the Lean project requires
    /// `LusternaLib`, and the translate agent gets the skill
    /// `solana-translate`.
    Solana {
        /// Absolute path to the Solanalib checkout.
        solanalib_root: PathBuf,
    },
}
