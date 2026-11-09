//!
//! ## Config vs. State Input
//!
//! Prefer a Config item over a Stage input whenever the item
//!
//! - is system dependent (e.g. a path on the machine), or
//! - is used by multiple stages
use std::{
    fs::{self, canonicalize},
    io,
    path::PathBuf,
    process,
};

use log::warn;
use serde::{Deserialize, Serialize};

use crate::{
    agent::Effort,
    utils::{
        cargo::{self, CargoError},
        git::{self, GitError},
    },
};

pub const DEFAULT_STAGE_MAX_ATTEMPTS: usize = 5;
pub const DEFAULT_MAX_BUDGED_USD: f64 = 50.0;

pub const DEFAULT_MODEL: &str = "opus";
pub const DEFAULT_EFFORT: Effort = Effort::Medium;

#[derive(clap::Args)]
pub struct Config {
    /// Maximum attempts per stage
    #[arg(long, global = true, default_value_t = DEFAULT_STAGE_MAX_ATTEMPTS)]
    pub(crate) stage_max_attempts: usize,
    /// Maximum budget per stage in USD
    #[arg(long, global = true, default_value_t = DEFAULT_MAX_BUDGED_USD)]
    pub(crate) stage_max_budget: f64,

    // Claude settings
    /// Claude model
    #[arg(long, global = true, default_value_t = DEFAULT_MODEL.to_string())]
    pub(crate) model: String,
    /// Claude effort level
    #[arg(long, global = true, value_enum, default_value_t = DEFAULT_EFFORT)]
    pub(crate) effort: Effort,

    /// Aeneas checkout
    #[arg(long, env = "AENEAS_ROOT")]
    aeneas_root: PathBuf,

    // Paths
    /// The path to the Solanalib library. Must be given if the tool targets a
    /// Solana program
    #[arg(long, global = true)]
    pub(crate) solanalib_root: Option<PathBuf>,

    /// Workspace of the Rust project
    #[arg(long, global = true, default_value = ".")]
    workspace_root: PathBuf,
    /// Crate inside the workspace. Required if the workspace has more than
    /// one crate
    #[arg(long, global = true)]
    crate_root: Option<PathBuf>,

    /// Run on a repository with uncommitted changes. The run commits them
    /// first, in a commit of their own.
    #[arg(long)]
    pub(crate) allow_dirty: bool,

    #[arg(long, help_heading = "Dev Options")]
    pub(crate) stop_after: Option<String>,
}

impl Config {
    pub fn manifest(&self) -> Result<PathBuf, ConfigError> {
        Ok(self.crate_root()?.join("Cargo.toml"))
    }

    pub fn workspace_root(&self) -> Result<PathBuf, ConfigError> {
        self.workspace_root.canonicalize().map_err(ConfigError::Io)
    }

    /// Returns `--crate-root` if given, and otherwise the only crate of the
    /// workspace.
    pub fn crate_root(&self) -> Result<PathBuf, ConfigError> {
        if let Some(crate_root) = &self.crate_root {
            return Ok(crate_root.canonicalize()?);
        }
        let members = cargo::workspace_members(&self.workspace_root()?.join("Cargo.toml"))?;
        match members.as_slice() {
            [crate_root] => Ok(crate_root.canonicalize()?),
            _ => Err(ConfigError::NoCrateRoot(members)),
        }
    }

    pub fn aeneas_root(&self) -> Result<PathBuf, ConfigError> {
        self.aeneas_root.canonicalize().map_err(ConfigError::Io)
    }

    pub fn repo_root(&self) -> Result<PathBuf, ConfigError> {
        git::toplevel(&self.workspace_root()?).map_err(ConfigError::Git)
    }

    pub fn lean_toolchain(&self) -> Result<String, ConfigError> {
        let toolchain_file = self.aeneas_root.join("backends/lean/lean-toolchain");
        let toolchain = fs::read_to_string(&toolchain_file)?;
        Ok(toolchain.trim().to_string())
    }

    pub fn get_platform(&self) -> Result<Platform, ConfigError> {
        if cargo::lock_has_package(&self.manifest()?, "solana-program")? {
            let solanalib_root = self
                .solanalib_root
                .as_ref()
                .ok_or(ConfigError::MissingSolanalibRoot)?;

            let lean_root = self.aeneas_root.join("backends/lean");
            if !lean_root.join(".lake/packages").is_dir() {
                return Err(ConfigError::MissingLakePackages(lean_root));
            }
            Ok(Platform::Solana {
                solanalib_root: canonicalize(solanalib_root)?,
            })
        } else {
            Ok(Platform::Generic)
        }
    }

    pub fn aeneas_rev(&self) -> Result<String, ConfigError> {
        Ok(git::head_rev(&self.aeneas_root)?)
    }

    pub fn ensure_aeneas_version_match(&self) -> Result<(), ConfigError> {
        let output = process::Command::new("aeneas").arg("-version").output()?;

        let version_bin = String::from_utf8_lossy(&output.stdout);

        let built_rev = version_bin
            .trim()
            .strip_prefix("aeneas ")
            .unwrap_or_default();
        let (built_rev, dirty) = match built_rev.strip_suffix("-dirty") {
            Some(rev) => (rev, true),
            None => (built_rev, false),
        };
        let checkout_rev = self.aeneas_rev()?;
        if built_rev.is_empty() || !checkout_rev.starts_with(built_rev) {
            return Err(ConfigError::AeneasVersionMismatch {
                version_bin: version_bin.to_string(),
                root: self.aeneas_root.clone(),
                version_checkout: checkout_rev,
            });
        }
        if dirty {
            warn!("`aeneas` on `PATH` was built from a checkout with uncommitted changes");
        }
        Ok(())
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

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error("the crate is a Solana program, so `--solanalib-root` is required")]
    MissingSolanalibRoot,
    #[error(
        "the workspace has {} crates, so `--crate-root` is required: {}",
        .0.len(),
        .0.iter().map(|c| c.display().to_string()).collect::<Vec<_>>().join(", ")
    )]
    NoCrateRoot(Vec<PathBuf>),
    #[error("`{}/.lake/packages` does not exist. Run `lake update` in `{}` once", .0.display(), .0.display())]
    MissingLakePackages(PathBuf),
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error(transparent)]
    Git(#[from] GitError),
    #[error(
        "`aeneas` on `PATH` reports version `{version_bin}`, but `{root}` is at {version_checkout}. Rebuild Aeneas or check out the matching commit"
    )]
    AeneasVersionMismatch {
        version_bin: String,
        root: PathBuf,
        version_checkout: String,
    },
}
