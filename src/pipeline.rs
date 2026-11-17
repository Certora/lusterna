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
//!    default opaque patterns and those of the spec passed to Charon. The
//!    pipeline writes the submission of the stage to `lusterna/translate.json`
//!    in the crate root.
//! 5. `formalize`: states each property as Lean theorems about the translation,
//!    without proofs. Runs only if the spec has properties. The pipeline writes
//!    the submission of the stage to `lusterna/formalize.json` in the crate
//!    root.
//! 6. `prove`: proves the theorems of the formalize stage, or shows that a
//!    theorem is false. Runs only if the spec has properties. It reads
//!    `lusterna/formalize.json`, and the pipeline writes the submission of the
//!    stage to `lusterna/prove.json` in the crate root.
//! 7. The pipeline writes the report of the run to `lusterna/report.md` in the
//!    crate root, from `lusterna/translate.json`, `lusterna/formalize.json` and
//!    `lusterna/prove.json`. The report shows a stage whose file is missing as
//!    skipped.
//!
//! A run starts only if the repository has no uncommitted changes other than
//! the spec file (unless `--allow-dirty`), the tools of `TOOLS` are on `PATH`,
//! and the settings that the stages need, such as `--solanalib-root` for a
//! Solana program, resolve.
//!
//! After each step, the pipeline commits all changes
//! in the repository.
//!
//! ## Partial Runs
//!
//! The `--start-at` option skips the steps before the named [`Step`], and the
//! `--stop-after` option ends the pipeline after the named [`Step`]. The report
//! is written even with `--stop-after`.

use std::{
    env, fs, io,
    path::{Path, PathBuf},
    time::Instant,
};

use log::info;
use serde::{Serialize, de::DeserializeOwned};

use crate::{
    config::{Config, ConfigError, Platform},
    spec::Spec,
    stage::{
        Stage, StageName,
        exec::{StageExec, StageExecutionError},
        formalize::{FormalizeInput, FormalizeStage, FormalizeSubmission},
        pre_translate::{PreTranslateInput, PreTranslateStage, PreTranslateSubmission},
        prove::{ProveInput, ProveStage, ProveSubmission},
        report,
        translate::{
            DEFAULT_OPAQUE, TranslateInput, TranslateStage, TranslateSubmission, solana_opaque,
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
    #[error("Error in Prove stage: {0}")]
    Prove(StageExecutionError<ProveStage>),
    /// Committing the work of a stage failed.
    #[error(transparent)]
    Git(#[from] GitError),
    /// A config value that a stage needs could not be resolved.
    #[error(transparent)]
    Config(#[from] ConfigError),
    /// The initial `cargo build` could not run.
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
    /// The file that holds the submission of the formalize stage does not
    /// parse.
    #[error("cannot parse the formalize submission in `{0}`: {1}")]
    FormalizeSubmission(PathBuf, serde_json::Error),
    /// A file that holds the submission of a stage does not parse.
    #[error("cannot parse the stage submission in `{0}`: {1}")]
    StageSubmission(PathBuf, serde_json::Error),
    /// The report templates do not render.
    #[error("cannot render the report: {0}")]
    Report(#[from] minijinja::Error),
}

/// The steps of a pipeline run, in the order in which they run. A step
/// executes a stage or does other work on the crate, and ends with a commit
/// of its changes.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, clap::ValueEnum)]
pub enum PipelineStep {
    Infer,
    /// Writes the spec and its properties to the crate.
    WriteSpec,
    PreTranslate,
    Translate,
    Formalize,
    Prove,
    /// Writes the report of the run to the crate. Runs even with
    /// `--stop-after`. `--start-at report` writes only the report.
    Report,
}

/// A pipeline of stages, configured by a spec. See the module documentation.
pub struct Pipeline {
    spec: Spec,
    /// The file that `spec` was read from.
    spec_path: PathBuf,
    config: Config,
    submissions: Submissions,
}

impl Pipeline {
    /// Creates the pipeline for `spec`, which was read from `spec_path`.
    pub fn new(spec: Spec, spec_path: PathBuf, config: Config) -> Result<Self, PipelineError> {
        let lusterna_dir = config.crate_root()?.join("lusterna");
        Ok(Self {
            spec,
            spec_path,
            config,
            submissions: Submissions::new(&lusterna_dir)?,
        })
    }

    /// Whether `step` is in the range that `--start-at` and `--stop-after`
    /// select.
    fn runs(&self, step: PipelineStep) -> bool {
        self.config.start_at.is_none_or(|s| s <= step)
            && self.config.stop_after.is_none_or(|s| step <= s)
    }

    /// Runs the steps of the pipeline in order and stops at the first error.
    pub fn run(mut self) -> Result<(), PipelineError> {
        self.pre_check()?;

        // 1. Infer the entrypoints and properties
        if self.runs(PipelineStep::Infer) && self.spec.infer {
            todo!()
        }

        // 2. Write the spec and its properties to the crate
        if self.runs(PipelineStep::WriteSpec) {
            self.write_spec()?;
        }

        // 3. Run the pre-translate stage
        // this is a noop if the project does build.
        if self.runs(PipelineStep::PreTranslate) {
            let submission = self.run_pretranslate()?;
            self.submissions.set_pre_translate(submission)?;
            self.commit(StageName::PreTranslate)?;
        }

        // 4. Run the translate stage
        if self.runs(PipelineStep::Translate) {
            if !self.runs(PipelineStep::PreTranslate) {
                // If pre translate was skipped, the crate might not build.
                // The translate stage fails in that so we stop early.
                let build = cargo::build(&self.config.manifest()?);
                if let Err(CargoError::Build { .. }) = &build {
                    log::error!(
                        "The crate does not build, cannot start at `translate`. Start at `pre-translate` or earlier."
                    );
                }
                build?;
            }
            if self.spec.entrypoints.is_empty() {
                return Err(PipelineError::NoEntrypoints);
            }
            let submission = self.run_translate(
                self.spec.entrypoints.clone(),
                opaque_patterns(&self.config, &self.spec.add_opaque)?,
            )?;
            self.submissions.set_translate(submission)?;
            self.commit(StageName::Translate)?;
        }

        // 5. Run the formalize stage
        if self.runs(PipelineStep::Formalize) && !self.spec.properties.0.is_empty() {
            let submission = self.run_formalize()?;
            self.submissions.set_formalize(submission)?;
            self.commit(StageName::Formalize)?;
        }

        // 6. Run the prove stage
        if self.runs(PipelineStep::Prove) && !self.spec.properties.0.is_empty() {
            let submission = self.run_prove()?;
            self.submissions.set_prove(submission)?;
            self.commit(StageName::Prove)?;
        }

        // 7. Write the report (always)
        self.write_report()
    }

    fn write_spec(&self) -> Result<(), PipelineError> {
        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        fs::create_dir_all(&lusterna_dir)?;
        fs::write(
            lusterna_dir.join("lusterna.json"),
            serde_json::to_string_pretty(&self.spec).expect("the spec serializes"),
        )?;
        fs::write(
            lusterna_dir.join("properties.json"),
            serde_json::to_string_pretty(&self.spec.properties).expect("the properties serialize"),
        )?;
        if let Some(sha) = git::commit_all(&self.config.repo_root()?, "Lusterna: spec")? {
            info!("Committed the spec in {}", sha);
        }
        Ok(())
    }

    fn commit(&self, stage: StageName) -> Result<(), PipelineError> {
        let name = stage.as_str().to_ascii_lowercase();
        match git::commit_all(&self.config.repo_root()?, "Lusterna: `formalize` stage")? {
            Some(sha) => info!("Committed `{}` work in {}", name, sha),
            None => info!("No `{}` work to commit", name),
        }
        Ok(())
    }

    fn run_pretranslate(&self) -> Result<PreTranslateSubmission, PipelineError> {
        let manifest = self.config.manifest()?;
        match cargo::build(&manifest) {
            Ok(_) => Ok(PreTranslateSubmission::default()),
            Err(CargoError::Build { stderr, .. }) => {
                info!("Crate does not build, executing pre translation stage");

                let input = PreTranslateInput {
                    workspace_root: self.config.workspace_root()?,
                    manifest: manifest.to_owned(),
                    build_stderr: stderr,
                };
                let submission = self
                    .exec_stage::<PreTranslateStage>(input)
                    .map_err(PipelineError::PreTranslate)?;

                Ok(submission)
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
    ) -> Result<TranslateSubmission, PipelineError> {
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
        let submission = self
            .exec_stage::<TranslateStage>(input)
            .map_err(PipelineError::Translate)?;

        Ok(submission)
    }

    fn run_formalize(&self) -> Result<FormalizeSubmission, PipelineError> {
        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        let lean_root = lusterna_dir.join("lean");
        let input = FormalizeInput {
            extracted: lean_root.join("Extraction"),
            lean_root,
            properties: lusterna_dir.join("properties.json"),
            crate_root: self.config.crate_root()?,
            entrypoints: self.spec.entrypoints.clone(),
        };
        let submission = self
            .exec_stage::<FormalizeStage>(input)
            .map_err(PipelineError::Formalize)?;

        Ok(submission)
    }

    fn run_prove(&self) -> Result<ProveSubmission, PipelineError> {
        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        let lean_root = lusterna_dir.join("lean");

        // Read the submission of the formalize stage, which `run_formalize`
        // wrote
        let formalize_path = lusterna_dir.join("formalize.json");
        let formalized: FormalizeSubmission =
            serde_json::from_str(&fs::read_to_string(&formalize_path)?)
                .map_err(|e| PipelineError::FormalizeSubmission(formalize_path, e))?;

        let input = ProveInput {
            extracted: lean_root.join("Extraction"),
            lean_root,
            properties: lusterna_dir.join("properties.json"),
            property_status: formalized.property_status,
            induction: formalized.induction,
        };
        let submission = self
            .exec_stage::<ProveStage>(input)
            .map_err(PipelineError::Prove)?;

        Ok(submission)
    }

    /// Writes the report of the run to `lusterna/report.md`, from the
    /// submissions of the stages in `lusterna/`.
    fn write_report(&mut self) -> Result<(), PipelineError> {
        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        fs::create_dir_all(&lusterna_dir)?;

        // Submissions
        let pre_translate = self.submissions.get_pre_translate();
        let translate = self.submissions.get_translate();
        let formalize = self.submissions.get_formalize();
        let prove = self.submissions.get_prove();

        let report = report::render(
            cargo::package_name(&self.config.manifest()?)?,
            &self.spec.properties,
            // The pipeline does not keep the submission of the pre-translate stage
            pre_translate,
            translate,
            formalize,
            prove,
        )?;
        fs::write(lusterna_dir.join("report.md"), report)?;

        match git::commit_all(&self.config.repo_root()?, "Lusterna: report")? {
            Some(sha) => info!("Committed the report in {}", sha),
            None => info!("No report changes to commit"),
        }
        Ok(())
    }

    fn exec_stage<S: Stage>(
        &self,
        input: S::Input,
    ) -> Result<S::Submission, StageExecutionError<S>> {
        let stage = S::setup(input).map_err(StageExecutionError::SetupError)?;
        let t = Instant::now();
        log::info!("Executing `{}`", S::NAME);
        let stage_exec = StageExec::new(stage);
        let submission = stage_exec.run(&self.config, None)?;
        log::info!("Executed `{}` in {}", S::NAME, format_elapsed(t));
        Ok(submission)
    }
}

pub(crate) struct Submissions {
    pre_translate: Option<PreTranslateSubmission>,
    translate: Option<TranslateSubmission>,
    formalize: Option<FormalizeSubmission>,
    prove: Option<ProveSubmission>,

    /// The directory where submissions are de-/serialized
    submission_dir: PathBuf,
}

impl Submissions {
    pub fn new(dir: &Path) -> io::Result<Self> {
        Ok(Self {
            pre_translate: Self::load(&Self::submission_path(dir, StageName::PreTranslate))?,
            translate: Self::load(&Self::submission_path(dir, StageName::Translate))?,
            formalize: Self::load(&Self::submission_path(dir, StageName::Formalize))?,
            prove: Self::load(&Self::submission_path(dir, StageName::Prove))?,
            submission_dir: dir.to_path_buf(),
        })
    }

    pub fn set_pre_translate(&mut self, submission: PreTranslateSubmission) -> io::Result<()> {
        self.write_submission(
            &Self::submission_path(&self.submission_dir, StageName::PreTranslate),
            &submission,
        )?;
        self.pre_translate = Some(submission);
        Ok(())
    }

    pub fn set_translate(&mut self, submission: TranslateSubmission) -> io::Result<()> {
        self.write_submission(
            &Self::submission_path(&self.submission_dir, StageName::Translate),
            &submission,
        )?;
        self.translate = Some(submission);
        Ok(())
    }

    pub fn set_formalize(&mut self, submission: FormalizeSubmission) -> io::Result<()> {
        self.write_submission(
            &Self::submission_path(&self.submission_dir, StageName::Formalize),
            &submission,
        )?;
        self.formalize = Some(submission);
        Ok(())
    }

    pub fn set_prove(&mut self, submission: ProveSubmission) -> io::Result<()> {
        assert!(self.prove.is_none());
        self.write_submission(
            &Self::submission_path(&self.submission_dir, StageName::Prove),
            &submission,
        )?;
        self.prove = Some(submission);
        Ok(())
    }

    pub fn get_pre_translate(&self) -> Option<&PreTranslateSubmission> {
        self.pre_translate.as_ref()
    }

    pub fn get_translate(&self) -> Option<&TranslateSubmission> {
        self.translate.as_ref()
    }

    pub fn get_formalize(&self) -> Option<&FormalizeSubmission> {
        self.formalize.as_ref()
    }

    pub fn get_prove(&self) -> Option<&ProveSubmission> {
        self.prove.as_ref()
    }

    fn submission_path(submission_dir: &Path, stage: StageName) -> PathBuf {
        let name = format!("{}.json", stage.to_string().to_lowercase());
        submission_dir.join(name)
    }

    fn write_submission<T: Serialize>(&self, path: &Path, submission: T) -> io::Result<()> {
        fs::write(
            path,
            serde_json::to_string_pretty(&submission).expect("the formalize submission serializes"),
        )
    }

    fn load<T: DeserializeOwned>(path: &Path) -> io::Result<Option<T>> {
        let text = match fs::read_to_string(path) {
            Ok(text) => text,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e.into()),
        };
        serde_json::from_str(&text)
            .map(Some)
            .map_err(io::Error::from)
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
