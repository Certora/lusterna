//! Runs the stages on a Rust crate, as its [`Spec`] says:
//!
//! 1. `infer`: not implemented. [`Pipeline::run`] panics if the spec asks for
//!    it.
//! 2. The pipeline writes the spec to `lusterna/lusterna.json` and its
//!    properties to `lusterna/properties.json` in the crate root.
//! 3. `pre-translate`: runs only if `cargo build` fails. An agent changes the
//!    crate so that it builds on the host, without changing what it computes on
//!    its target.
//! 4. `translate`: translates the code reachable from the entrypoints, with the
//!    default opaque patterns and those of the spec passed to Charon.
//! 5. `formalize`: states each property as Lean theorems about the translation,
//!    without proofs. Runs only if the spec has properties.
//!
//! A run starts only if the repository has no uncommitted changes other than
//! the spec file (unless `--allow-dirty`), the tools of `TOOLS` are on `PATH`,
//! and the settings that the stages need, such as `--solanalib-root` for a
//! Solana program, resolve. After each step, the pipeline commits all changes
//! in the repository. The `--stop-after` option ends the pipeline after the
//! named stage.

use std::{env, fs, io, path::PathBuf, time::Instant};

use log::{info, warn};

use crate::{
    config::{Config, ConfigError, Platform},
    spec::Spec,
    stage::{
        Stage, StageName,
        exec::{StageExec, StageExecutionError},
        formalize::{FormalizeInput, FormalizeOutput, FormalizeStage},
        pre_translate::{PreTranslateInput, PreTranslateStage},
        translate::{
            DEFAULT_OPAQUE, TranslateInput, TranslateOutput, TranslateStage, solana_opaque,
        },
    },
    utils::{
        cargo::{self, CargoError},
        format_elapsed,
        git::{self, GitError},
    },
};

/// The tools that the stages run.
const TOOLS: [&str; 6] = ["git", "cargo", "charon", "aeneas", "lake", "claude"];

/// An error that ends a pipeline run.
#[derive(Debug, thiserror::Error)]
pub enum PipelineError {
    /// The `pre-translate` stage failed to set up or to finish.
    #[error("Error in PreTranslate stage: {0}")]
    PreTranslate(StageExecutionError<PreTranslateStage>),
    /// The `translate` stage failed to set up or to finish.
    #[error("Error in Translate stage: {0}")]
    Translate(StageExecutionError<TranslateStage>),
    #[error("Error in Formalize stage: {0}")]
    Formalize(StageExecutionError<FormalizeStage>),
    /// Committing the work of a stage failed.
    #[error(transparent)]
    Git(#[from] GitError),
    /// A config value that a stage needs could not be resolved.
    #[error(transparent)]
    Config(#[from] ConfigError),
    /// The initial `cargo build` could not run. A build that runs and fails
    /// starts the `pre-translate` stage instead.
    #[error(transparent)]
    Cargo(#[from] CargoError),
    /// The repository has uncommitted changes to these files, relative to its
    /// root, other than the spec file.
    #[error(
        "commit or stash the changes to these files before running lusterna: {}",
        .0.iter().map(|f| f.display().to_string()).collect::<Vec<_>>().join(", ")
    )]
    DirtyWorktree(Vec<PathBuf>),
    /// These tools of `TOOLS` are not on `PATH`.
    #[error("these tools are not on `PATH`: {}", .0.join(", "))]
    NotInPath(Vec<&'static str>),
    /// The spec has no entrypoints and does not ask to infer them.
    #[error("the spec has no entrypoints; add some or set `infer` to true")]
    NoEntrypoints,
    /// Writing the spec or the properties to the crate failed.
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
}

/// A pipeline of stages, configured by a spec. See the module documentation.
pub struct Pipeline {
    spec: Spec,
    /// The file that `spec` was read from.
    spec_path: PathBuf,
    config: Config,
    stop_after: Option<StageName>,
}

impl Pipeline {
    fn stop_after(config: &Config) -> Option<StageName> {
        match &config.stop_after {
            Some(sa) => match StageName::try_from(sa.as_str()) {
                Ok(sa) => Some(sa),
                Err(_) => {
                    warn!("Invalid name for --stop-after: '{}', skipping", sa);
                    None
                }
            },
            None => None,
        }
    }

    /// Creates the pipeline for `spec`, which was read from `spec_path`. An
    /// invalid `--stop-after` stage name is logged and ignored.
    pub fn new(spec: Spec, spec_path: PathBuf, config: Config) -> Self {
        let stop_after = Self::stop_after(&config);
        Self {
            spec,
            spec_path,
            config,
            stop_after,
        }
    }

    /// Runs the stages of the pipeline in order and stops at the first error.
    pub fn run(self) -> Result<(), PipelineError> {
        self.pre_check()?;
        let repo_root = self.config.repo_root()?;

        // 1. Infer the entrypoints and properties
        if self.spec.infer {
            todo!()
        }
        if self.spec.entrypoints.is_empty() {
            return Err(PipelineError::NoEntrypoints);
        }

        // 2. Write the spec and its properties to the crate
        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        fs::create_dir_all(&lusterna_dir)?;
        let properties = lusterna_dir.join("properties.json");
        fs::write(
            lusterna_dir.join("lusterna.json"),
            serde_json::to_string_pretty(&self.spec).expect("the spec serializes"),
        )?;
        fs::write(
            &properties,
            serde_json::to_string_pretty(&self.spec.properties).expect("the properties serialize"),
        )?;
        if let Some(sha) = git::commit_all(&repo_root, "Lusterna: spec")? {
            info!("Committed the spec in {}", sha);
        }

        // 3. Run the pre-translate stage
        // this is a noop if the project does build.
        self.run_pretranslate()?;
        if matches!(&self.stop_after, Some(StageName::PreTranslate)) {
            return Ok(());
        }

        // 4. Run the translate stage
        let translate_out = self.run_translate(
            self.spec.entrypoints.clone(),
            opaque_patterns(&self.config, &self.spec.add_opaque)?,
        )?;
        if matches!(&self.stop_after, Some(StageName::Translate))
            || self.spec.properties.0.is_empty()
        {
            return Ok(());
        }

        // 5. Run the formalize stage
        self.run_formalize(translate_out, properties)?;
        Ok(())
    }

    fn run_pretranslate(&self) -> Result<(), PipelineError> {
        let manifest = self.config.manifest()?;
        match cargo::build(&manifest) {
            Ok(_) => Ok(()),
            Err(CargoError::Build { stderr, .. }) => {
                info!("Crate does not build, executing pre translation stage");

                let input = PreTranslateInput {
                    workspace_root: self.config.workspace_root()?,
                    manifest: manifest.to_owned(),
                    build_stderr: stderr,
                };
                self.exec_stage::<PreTranslateStage>(input)
                    .map_err(PipelineError::PreTranslate)?;

                match git::commit_all(&self.config.repo_root()?, "Lusterna: `pre-translate` stage")?
                {
                    Some(sha) => {
                        info!("Committed `pre-translate` work in {}", sha);
                        Ok(())
                    }
                    None => {
                        info!("No `pre-translate` work to commit");
                        Ok(())
                    }
                }
            }
            Err(e) => return Err(PipelineError::Cargo(e)),
        }
    }

    /// Checks everything a run needs before it does any work.
    fn pre_check(&self) -> Result<(), PipelineError> {
        self.check_dependencies()?;
        self.check_clean_worktree()
    }

    /// Checks that the repository has no uncommitted changes but the spec,
    /// which the spec commit includes. With `--allow-dirty`, commits the
    /// changes instead.
    fn check_clean_worktree(&self) -> Result<(), PipelineError> {
        let repo_root = self.config.repo_root()?;
        let files: Vec<PathBuf> = git::uncommitted_files(&repo_root)?
            .into_iter()
            .filter(|f| repo_root.join(f) != self.spec_path)
            .collect();
        if files.is_empty() {
            return Ok(());
        }
        if !self.config.allow_dirty {
            return Err(PipelineError::DirtyWorktree(files));
        }
        if let Some(sha) = git::commit_all(&repo_root, "Lusterna: uncommitted changes")? {
            info!("Committed the uncommitted changes in {}", sha);
        }
        Ok(())
    }

    /// Checks that the tools of `TOOLS` are on `PATH`, that `aeneas` matches
    /// the Aeneas checkout, and that the settings that the stages need
    /// resolve, such as `--solanalib-root` for a Solana program.
    fn check_dependencies(&self) -> Result<(), PipelineError> {
        let path = env::var_os("PATH").unwrap_or_default();
        let missing: Vec<&str> = TOOLS
            .into_iter()
            .filter(|tool| !env::split_paths(&path).any(|dir| dir.join(tool).is_file()))
            .collect();
        if !missing.is_empty() {
            return Err(PipelineError::NotInPath(missing));
        }
        self.config.ensure_aeneas_version_match()?;
        self.config.crate_root()?;
        self.config.aeneas_rev()?;
        self.config.lean_toolchain()?;
        self.config.get_platform()?;
        Ok(())
    }

    fn run_translate(
        &self,
        entrypoints: Vec<String>,
        opaque: Vec<String>,
    ) -> Result<TranslateOutput, PipelineError> {
        let input = TranslateInput {
            workspace_root: self.config.workspace_root()?,
            crate_root: self.config.crate_root()?,
            entrypoints,
            opaque,
            aeneas_root: self.config.aeneas_root()?,
            aeneas_rev: self.config.aeneas_rev()?,
            lean_toolchain: self.config.lean_toolchain()?,
            platform: self.config.get_platform()?,
        };
        let output = self
            .exec_stage::<TranslateStage>(input)
            .map_err(PipelineError::Translate)?;

        match git::commit_all(&self.config.repo_root()?, "Lusterna: `translate` stage")? {
            Some(sha) => info!("Committed `translate` work in {}", sha),
            None => info!("No `translate` work to commit"),
        }
        Ok(output)
    }

    fn run_formalize(
        &self,
        translate_out: TranslateOutput,
        properties: PathBuf,
    ) -> Result<FormalizeOutput, PipelineError> {
        let input = FormalizeInput {
            extracted: translate_out.extracted,
            lean_root: translate_out.lean_root,
            properties,
        };
        let output = self
            .exec_stage::<FormalizeStage>(input)
            .map_err(PipelineError::Formalize)?;

        match git::commit_all(&self.config.repo_root()?, "Lusterna: `formalize` stage")? {
            Some(sha) => info!("Committed `formalize` work in {}", sha),
            None => info!("No `formalize` work to commit"),
        }
        Ok(output)
    }

    fn exec_stage<S: Stage>(&self, input: S::Input) -> Result<S::Output, StageExecutionError<S>> {
        let stage = S::setup(input).map_err(StageExecutionError::SetupError)?;
        let t = Instant::now();
        log::info!("Executing `{}`", S::NAME);
        let stage_exec = StageExec::new(stage);
        let output = stage_exec.run(&self.config, None)?;
        log::info!("Executed `{}` in {}", S::NAME, format_elapsed(t));
        Ok(output)
    }
}

/// Returns the Charon `--opaque` patterns: the default patterns, the Solana
/// patterns if the crate is a Solana program, and `add_opaque`.
fn opaque_patterns(config: &Config, add_opaque: &[String]) -> Result<Vec<String>, ConfigError> {
    let mut opaque: Vec<String> = DEFAULT_OPAQUE.lines().map(str::to_owned).collect();
    if let Platform::Solana { .. } = config.get_platform()? {
        opaque.extend(solana_opaque());
    }
    opaque.extend(add_opaque.iter().cloned());
    Ok(opaque)
}
