//! Runs the stages on a Rust crate, as its [`Spec`] says:
//!
//! 1. `infer`: runs only if the spec asks for it. An agent finds properties and
//!    entrypoints in addition to those of the spec. The pipeline writes the
//!    submission of the stage to `lusterna/infer.json` in the crate root, and
//!    the later steps use its properties and entrypoints.
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
//! the spec file (unless `--allow-dirty`), the tools of `TOOLS` (and on Linux
//! `LINUX_SANDBOX_TOOLS`) are on `PATH`, and the settings that the stages need
//! resolve.
//!
//! After each step, the pipeline commits all changes
//! in the repository.
//!
//! ## Partial Runs
//!
//! The `--stop-after` option ends the pipeline after the named [`Step`]. The
//! report is written even with `--stop-after`.

use std::{
    env, fs, io,
    path::{Path, PathBuf},
    time::Instant,
};

use log::info;
use serde::{Deserialize, Serialize, de::DeserializeOwned};

use crate::{
    config::{Config, ConfigError, Platform},
    spec::Spec,
    stage::{
        Stage, StageName,
        exec::{StageExec, StageExecutionError},
        formalize::{FormalizeInput, FormalizeStage, FormalizeSubmission},
        infer::{InferStage, InferStageInput, InferStageSubmission},
        pre_translate::{PreTranslateInput, PreTranslateStage, PreTranslateSubmission},
        prove::{ProveInput, ProveStage, ProveSubmission, formalized_properties},
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

/// The tools that the `claude` sandbox needs on Linux.
const LINUX_SANDBOX_TOOLS: [&str; 2] = ["bwrap", "socat"];

/// The content of `lusterna/.gitignore` in the crate root.
const GITIGNORE: &str = "/lean/.lake/\n/extraction.llbc\n/charon/\n/run.json\n/*.state.json\n";

/// An error that ends a pipeline run.
#[derive(Debug, thiserror::Error)]
pub enum PipelineError {
    #[error("Error in Infer stage: {0}")]
    Infer(StageExecutionError<InferStage>),
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
    /// These tools of `TOOLS` or `LINUX_SANDBOX_TOOLS` are not on `PATH`.
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
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, clap::ValueEnum, Serialize, Deserialize)]
pub enum PipelineStep {
    Infer,
    /// Writes the spec and its properties to the crate.
    WriteSpec,
    PreTranslate,
    Translate,
    Formalize,
    Prove,
    /// Writes the report of the run to the crate. Runs even with
    /// `--stop-after`.
    Report,
}

/// The state of an unfinished run.
#[derive(Serialize, Deserialize)]
struct RunState {
    spec: Spec,
    /// The file that `spec` was read from.
    spec_path: PathBuf,
    /// The commit of the repository that the run started from.
    commit: String,
    /// The step that runs next.
    next: PipelineStep,
    /// `HEAD` of the repository when the state was written.
    head: String,
}

/// A pipeline of stages, configured by a spec. See the module documentation.
pub struct Pipeline {
    spec: Spec,
    /// The file that `spec` was read from.
    spec_path: PathBuf,
    config: Config,
    submissions: Submissions,
    /// The commit of the repository that the run starts from.
    /// Recorded after `pre_check` passed.
    commit: Option<String>,
    /// The step that runs next.
    next: PipelineStep,
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
            commit: None,
            next: PipelineStep::Infer,
        })
    }

    /// Whether `step` is `next` or a later step, and runs with
    /// `--stop-after`.
    fn runs(&self, step: PipelineStep) -> bool {
        self.next <= step && self.config.stop_after.is_none_or(|s| step <= s)
    }

    /// Writes the state of the run to `lusterna/run.json` in the crate root.
    fn save_state(&self) -> Result<(), PipelineError> {
        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        fs::create_dir_all(&lusterna_dir)?;
        let state = RunState {
            spec: self.spec.clone(),
            spec_path: self.spec_path.clone(),
            commit: self.commit.clone().expect("`run` sets the commit"),
            next: self.next,
            head: git::head_rev(&self.config.repo_root()?)?,
        };
        fs::write(
            lusterna_dir.join("run.json"),
            serde_json::to_string_pretty(&state).expect("the run state serializes"),
        )?;
        Ok(())
    }

    /// Sets `next` to the step that runs next and saves the state of the run.
    fn step_done(&mut self, next: PipelineStep) -> Result<(), PipelineError> {
        self.next = next;
        self.save_state()
    }

    /// Saves the state of the run if `--stop-after` ended it before the
    /// `prove` step, and deletes `lusterna/run.json` otherwise.
    fn close_state(&self) -> Result<(), PipelineError> {
        if self
            .config
            .stop_after
            .is_some_and(|s| s < PipelineStep::Prove)
        {
            return self.save_state();
        }
        fs::remove_file(self.config.crate_root()?.join("lusterna/run.json"))?;
        Ok(())
    }

    /// Starts a new run: deletes the submissions and the stage states of an
    /// earlier run, then runs the steps of the pipeline.
    pub fn run(mut self) -> Result<(), PipelineError> {
        self.pre_check()?;
        self.commit = Some(git::head_rev(&self.config.repo_root()?)?);

        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        fs::create_dir_all(&lusterna_dir)?;
        self.submissions.clear()?;
        remove_stage_states(&lusterna_dir)?;
        fs::write(lusterna_dir.join(".gitignore"), GITIGNORE)?;
        self.save_state()?;

        self.run_steps()
    }

    /// Runs the steps of the pipeline from `next` on, in order, and stops at
    /// the first error.
    fn run_steps(mut self) -> Result<(), PipelineError> {
        // 1. Infer the entrypoints and properties
        if self.spec.infer {
            if self.runs(PipelineStep::Infer) {
                let submission = self.run_infer()?;
                self.submissions.set_infer(submission)?;
                self.commit(StageName::Infer)?;
                self.step_done(PipelineStep::WriteSpec)?;
            }
            // The later steps use the inferred properties and entrypoints.
            let inferred = self
                .submissions
                .get_infer()
                .expect("the infer step stored the submission");
            self.spec.properties = inferred.properties.clone();
            self.spec.entrypoints = inferred.entrypoints.clone();
        }

        // 2. Write the spec and its properties to the crate
        if self.runs(PipelineStep::WriteSpec) {
            self.write_spec()?;
            self.step_done(PipelineStep::PreTranslate)?;
        }

        // 3. Run the pre-translate stage
        // this is a noop if the project does build.
        if self.runs(PipelineStep::PreTranslate) {
            let submission = self.run_pretranslate()?;
            self.submissions.set_pre_translate(submission)?;
            self.commit(StageName::PreTranslate)?;
            self.step_done(PipelineStep::Translate)?;
        }

        // 4. Run the translate stage
        if self.runs(PipelineStep::Translate) {
            if self.spec.entrypoints.is_empty() {
                return Err(PipelineError::NoEntrypoints);
            }
            let submission = self.run_translate(
                self.spec.entrypoints.clone(),
                opaque_patterns(&self.config, &self.spec.add_opaque)?,
            )?;
            self.submissions.set_translate(submission)?;
            self.commit(StageName::Translate)?;
            self.step_done(PipelineStep::Formalize)?;
        }

        // 5. Run the formalize stage
        if self.runs(PipelineStep::Formalize) && !self.spec.properties.0.is_empty() {
            let submission = self.run_formalize()?;
            self.submissions.set_formalize(submission)?;
            self.commit(StageName::Formalize)?;
            self.step_done(PipelineStep::Prove)?;
        }

        // 6. Run the prove stage
        if self.runs(PipelineStep::Prove) && !self.spec.properties.0.is_empty() {
            let submission = self.run_prove()?;
            self.submissions.set_prove(submission)?;
            self.commit(StageName::Prove)?;
            self.step_done(PipelineStep::Report)?;
        }

        // 7. Write the report (always)
        self.write_report()?;
        self.close_state()
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
        let msg = format!("Lusterna: `{}` stage", name);
        match git::commit_all(&self.config.repo_root()?, &msg)? {
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
            Err(e) => Err(PipelineError::Cargo(e)),
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

    /// Checks that the tools of `TOOLS` (and on Linux `LINUX_SANDBOX_TOOLS`)
    /// are on `PATH`, that `aeneas` matches the Aeneas checkout, and that the
    /// settings.
    fn check_dependencies(&self) -> Result<(), PipelineError> {
        let path = env::var_os("PATH").unwrap_or_default();
        let mut tools = TOOLS.to_vec();
        if cfg!(target_os = "linux") {
            tools.extend(LINUX_SANDBOX_TOOLS);
        }
        let missing: Vec<&str> = tools
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
            solanlib_root: self.config.solanalib_root()?,
        };
        let submission = self
            .exec_stage::<TranslateStage>(input)
            .map_err(PipelineError::Translate)?;

        Ok(submission)
    }

    fn run_infer(&self) -> Result<InferStageSubmission, PipelineError> {
        let input = InferStageInput {
            crate_root: self.config.crate_root()?,
            existing_properties: self.spec.properties.clone(),
            existing_entrypoints: self.spec.entrypoints.clone(),
            design_doc: self.spec.design_doc.clone(),
            platform: self.config.get_platform()?,
        };
        let submission = self
            .exec_stage::<InferStage>(input)
            .map_err(PipelineError::Infer)?;

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
            crate_root: self.config.crate_root()?,
            formalized: formalized_properties(&self.spec.properties, formalized.property_status),
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
        let t = Instant::now();
        let lusterna_dir = self.config.crate_root()?.join("lusterna");
        fs::create_dir_all(&lusterna_dir)?;

        // Submissions
        let pre_translate = self.submissions.get_pre_translate();
        let translate = self.submissions.get_translate();
        let formalize = self.submissions.get_formalize();
        let prove = self.submissions.get_prove();

        let report = report::render(
            cargo::package_name(&self.config.manifest()?)?,
            self.commit.as_deref().expect("`run` sets the commit"),
            &self.config.aeneas_rev()?,
            &self.spec.entrypoints,
            &self.spec.properties,
            // The pipeline does not keep the submission of the pre-translate stage
            pre_translate,
            translate,
            formalize,
            prove,
        )?;
        fs::write(lusterna_dir.join("report.md"), report)?;
        info!("Wrote the report in {}", format_elapsed(t));

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
        let stage_exec = StageExec::new(stage, &self.config)?;
        let submission = stage_exec.run(&self.config, None)?;
        log::info!("Executed `{}` in {}", S::NAME, format_elapsed(t));
        Ok(submission)
    }
}

pub(crate) struct Submissions {
    infer: Option<InferStageSubmission>,
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
            infer: Self::load(&Self::submission_path(dir, StageName::Infer))?,
            pre_translate: Self::load(&Self::submission_path(dir, StageName::PreTranslate))?,
            translate: Self::load(&Self::submission_path(dir, StageName::Translate))?,
            formalize: Self::load(&Self::submission_path(dir, StageName::Formalize))?,
            prove: Self::load(&Self::submission_path(dir, StageName::Prove))?,
            submission_dir: dir.to_path_buf(),
        })
    }

    /// Deletes the submission files and the loaded submissions.
    pub fn clear(&mut self) -> io::Result<()> {
        let stages = [
            StageName::Infer,
            StageName::PreTranslate,
            StageName::Translate,
            StageName::Formalize,
            StageName::Prove,
        ];
        for stage in stages {
            match fs::remove_file(Self::submission_path(&self.submission_dir, stage)) {
                Err(e) if e.kind() != io::ErrorKind::NotFound => return Err(e),
                _ => {}
            }
        }
        self.infer = None;
        self.pre_translate = None;
        self.translate = None;
        self.formalize = None;
        self.prove = None;
        Ok(())
    }

    pub fn set_infer(&mut self, submission: InferStageSubmission) -> io::Result<()> {
        // The infer step runs before the step that creates the directory
        fs::create_dir_all(&self.submission_dir)?;
        self.write_submission(
            &Self::submission_path(&self.submission_dir, StageName::Infer),
            &submission,
        )?;
        self.infer = Some(submission);
        Ok(())
    }

    pub fn get_infer(&self) -> Option<&InferStageSubmission> {
        self.infer.as_ref()
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
            Err(e) => return Err(e),
        };
        serde_json::from_str(&text)
            .map(Some)
            .map_err(io::Error::from)
    }
}

/// Deletes every `*.state.json` file in `dir`.
fn remove_stage_states(dir: &Path) -> io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if name.ends_with(".state.json") {
            fs::remove_file(&path)?;
        }
    }
    Ok(())
}

/// Returns the Charon `--opaque` patterns: the default patterns, the Solana
/// patterns if the crate is a Solana program, and `add_opaque`.
fn opaque_patterns(config: &Config, add_opaque: &[String]) -> Result<Vec<String>, ConfigError> {
    let mut opaque: Vec<String> = DEFAULT_OPAQUE.lines().map(str::to_owned).collect();
    if let Platform::Solana = config.get_platform()? {
        opaque.extend(solana_opaque());
    }
    opaque.extend(add_opaque.iter().cloned());
    Ok(opaque)
}

#[cfg(test)]
mod tests {
    use std::process::Command;

    use clap::ValueEnum;
    use tempfile::TempDir;

    use super::*;
    use crate::utils::parse_config;

    struct Fixture {
        pipeline: Pipeline,
        /// The root of the repository, which holds the crate
        root: PathBuf,
        _dir: TempDir,
    }

    /// A pipeline for a crate at the root of a new repository with one commit,
    /// with the spec `spec` and the command-line arguments `args`
    fn fixture(spec: &str, args: &[&str]) -> Fixture {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        fs::create_dir(root.join("src")).unwrap();
        fs::write(root.join("src/lib.rs"), "").unwrap();
        fs::write(
            root.join("Cargo.toml"),
            "[package]\nname = \"demo\"\nversion = \"0.1.0\"\nedition = \"2024\"\n",
        )
        .unwrap();
        fs::write(root.join("Cargo.lock"), "").unwrap();
        fs::write(root.join("lusterna.json"), spec).unwrap();
        let init = Command::new("git")
            .args(["init", "-q"])
            .current_dir(&root)
            .status()
            .unwrap();
        assert!(init.success());
        git::commit_all(&root, "init").unwrap();

        let root_arg = root.display().to_string();
        let mut config_args = vec![
            "--workspace-root".to_owned(),
            root_arg.clone(),
            "--crate-root".to_owned(),
            root_arg,
        ];
        config_args.extend(args.iter().map(|a| a.to_string()));
        let config = parse_config(config_args);

        Fixture {
            // Directly construct the Pipeline without calling "new",
            // this skip the pre-checks, which can fail in the test environment.
            pipeline: Pipeline {
                spec: serde_json::from_str(spec).unwrap(),
                spec_path: root.join("lusterna.json"),
                submissions: Submissions::new(&root.join("lusterna")).unwrap(),
                config,
                commit: None,
                next: PipelineStep::Infer,
            },
            root,
            _dir: dir,
        }
    }

    fn last_commit_message(repo: &Path) -> String {
        let output = Command::new("git")
            .args(["log", "-1", "--format=%s"])
            .current_dir(repo)
            .output()
            .unwrap();
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    }

    #[test]
    fn runs_selects_the_steps_to_stop_after() {
        let cases: [(&[&str], &[&str]); 3] = [
            (
                &[],
                &[
                    "infer",
                    "write-spec",
                    "pre-translate",
                    "translate",
                    "formalize",
                    "prove",
                    "report",
                ],
            ),
            (
                &["--stop-after", "formalize"],
                &[
                    "infer",
                    "write-spec",
                    "pre-translate",
                    "translate",
                    "formalize",
                ],
            ),
            (
                &["--stop-after", "translate"],
                &["infer", "write-spec", "pre-translate", "translate"],
            ),
        ];
        for (args, expected) in cases {
            let f = fixture("{}", args);
            let running: Vec<String> = PipelineStep::value_variants()
                .iter()
                .filter(|step| f.pipeline.runs(**step))
                .map(|step| step.to_possible_value().unwrap().get_name().to_owned())
                .collect();
            assert_eq!(running, expected, "{args:?}");
        }
    }

    #[test]
    fn runs_skips_the_steps_before_next() {
        let mut f = fixture("{}", &["--stop-after", "formalize"]);
        f.pipeline.next = PipelineStep::Translate;

        let running: Vec<String> = PipelineStep::value_variants()
            .iter()
            .filter(|step| f.pipeline.runs(**step))
            .map(|step| step.to_possible_value().unwrap().get_name().to_owned())
            .collect();
        assert_eq!(running, ["translate", "formalize"]);
    }

    #[test]
    fn save_state_writes_the_run_state() {
        let mut f = fixture(r#"{"entrypoints": ["demo::f"]}"#, &[]);
        f.pipeline.commit = Some("start".to_owned());
        f.pipeline.next = PipelineStep::Translate;
        f.pipeline.save_state().unwrap();

        let text = fs::read_to_string(f.root.join("lusterna/run.json")).unwrap();
        let state: RunState = serde_json::from_str(&text).unwrap();
        assert_eq!(state.spec.entrypoints, ["demo::f"]);
        assert_eq!(state.spec_path, f.root.join("lusterna.json"));
        assert_eq!(state.commit, "start");
        assert!(state.next == PipelineStep::Translate);
        assert_eq!(state.head, git::head_rev(&f.root).unwrap());
    }

    #[test]
    fn step_done_sets_next_and_saves_the_state() {
        let mut f = fixture("{}", &[]);
        f.pipeline.commit = Some("start".to_owned());
        f.pipeline.step_done(PipelineStep::Formalize).unwrap();

        assert!(f.pipeline.next == PipelineStep::Formalize);
        let text = fs::read_to_string(f.root.join("lusterna/run.json")).unwrap();
        let state: RunState = serde_json::from_str(&text).unwrap();
        assert!(state.next == PipelineStep::Formalize);
    }

    #[test]
    fn close_state_deletes_the_state_of_a_finished_run() {
        for args in [&[][..], &["--stop-after", "prove"], &["--stop-after", "report"]] {
            let mut f = fixture("{}", args);
            f.pipeline.commit = Some("start".to_owned());
            f.pipeline.save_state().unwrap();

            f.pipeline.close_state().unwrap();
            assert!(!f.root.join("lusterna/run.json").exists(), "{args:?}");
        }
    }

    #[test]
    fn close_state_saves_the_state_of_a_run_that_stopped_before_prove() {
        let mut f = fixture("{}", &["--stop-after", "formalize"]);
        f.pipeline.commit = Some("start".to_owned());
        f.pipeline.next = PipelineStep::Prove;
        f.pipeline.save_state().unwrap();
        fs::write(f.root.join("report.md"), "").unwrap();
        git::commit_all(&f.root, "Lusterna: report").unwrap();

        f.pipeline.close_state().unwrap();
        let text = fs::read_to_string(f.root.join("lusterna/run.json")).unwrap();
        let state: RunState = serde_json::from_str(&text).unwrap();
        assert!(state.next == PipelineStep::Prove);
        assert_eq!(state.head, git::head_rev(&f.root).unwrap());
    }

    #[test]
    fn clear_deletes_the_submission_files_and_the_loaded_submissions() {
        let dir = tempfile::tempdir().unwrap();
        let mut submissions = Submissions::new(dir.path()).unwrap();
        submissions
            .set_pre_translate(PreTranslateSubmission::default())
            .unwrap();
        fs::write(dir.path().join("prove.json"), "stale").unwrap();
        fs::write(dir.path().join("report.md"), "report").unwrap();

        submissions.clear().unwrap();
        assert!(submissions.get_pre_translate().is_none());
        assert!(!dir.path().join("pretranslate.json").exists());
        assert!(!dir.path().join("prove.json").exists());
        assert!(dir.path().join("report.md").exists());
        assert!(Submissions::new(dir.path()).unwrap().get_prove().is_none());
    }

    #[test]
    fn remove_stage_states_deletes_only_the_state_files() {
        let dir = tempfile::tempdir().unwrap();
        for name in ["translate.state.json", "prove.state.json", "translate.json"] {
            fs::write(dir.path().join(name), "").unwrap();
        }

        remove_stage_states(dir.path()).unwrap();
        assert!(!dir.path().join("translate.state.json").exists());
        assert!(!dir.path().join("prove.state.json").exists());
        assert!(dir.path().join("translate.json").exists());
    }

    #[test]
    fn submission_files_are_named_after_their_stage() {
        let dir = Path::new("/lusterna");
        let cases = [
            (StageName::Infer, "infer.json"),
            (StageName::PreTranslate, "pretranslate.json"),
            (StageName::Translate, "translate.json"),
            (StageName::Formalize, "formalize.json"),
            (StageName::Prove, "prove.json"),
        ];
        for (stage, file) in cases {
            assert_eq!(Submissions::submission_path(dir, stage), dir.join(file));
        }
    }

    #[test]
    fn stored_submissions_load_in_new_submissions() {
        let dir = tempfile::tempdir().unwrap();
        let lusterna_dir = dir.path().join("lusterna");
        let infer = InferStageSubmission {
            properties: Default::default(),
            entrypoints: vec!["demo::f".to_owned()],
            design_doc: None,
        };

        let mut submissions = Submissions::new(&lusterna_dir).unwrap();
        submissions.set_infer(infer.clone()).unwrap();
        submissions
            .set_pre_translate(PreTranslateSubmission::default())
            .unwrap();

        let loaded = Submissions::new(&lusterna_dir).unwrap();
        assert_eq!(
            serde_json::to_value(loaded.get_infer()).unwrap(),
            serde_json::to_value(Some(&infer)).unwrap()
        );
        assert!(loaded.get_pre_translate().is_some());
        assert!(loaded.get_translate().is_none());
        assert!(loaded.get_formalize().is_none());
        assert!(loaded.get_prove().is_none());
    }

    #[test]
    fn malformed_submission_file_fails_to_load() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("translate.json"), "not json").unwrap();

        let error = Submissions::new(dir.path()).err().unwrap();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn opaque_patterns_of_a_generic_crate_are_the_defaults_and_the_added_ones() {
        let f = fixture("{}", &[]);
        let added = ["demo::hidden".to_owned()];

        let mut expected: Vec<String> = DEFAULT_OPAQUE.lines().map(str::to_owned).collect();
        expected.push("demo::hidden".to_owned());
        assert_eq!(
            opaque_patterns(&f.pipeline.config, &added).unwrap(),
            expected
        );
    }

    #[test]
    fn clean_worktree_passes() {
        let f = fixture("{}", &[]);
        f.pipeline.check_clean_worktree().unwrap();
    }

    #[test]
    fn changed_spec_file_passes() {
        let f = fixture("{}", &[]);
        fs::write(f.root.join("lusterna.json"), r#"{"infer": true}"#).unwrap();
        f.pipeline.check_clean_worktree().unwrap();
    }

    #[test]
    fn other_changes_fail_with_the_changed_files() {
        let f = fixture("{}", &[]);
        fs::write(f.root.join("src/lib.rs"), "pub fn f() {}").unwrap();
        fs::write(f.root.join("new.rs"), "").unwrap();

        let error = f.pipeline.check_clean_worktree().unwrap_err();
        let PipelineError::DirtyWorktree(mut files) = error else {
            panic!("expected `DirtyWorktree`, got {error}");
        };
        files.sort();
        assert_eq!(
            files,
            [PathBuf::from("new.rs"), PathBuf::from("src/lib.rs")]
        );
    }

    #[test]
    fn allow_dirty_commits_the_changes() {
        let f = fixture("{}", &["--allow-dirty"]);
        fs::write(f.root.join("src/lib.rs"), "pub fn f() {}").unwrap();

        f.pipeline.check_clean_worktree().unwrap();
        assert!(git::is_clean(&f.root).unwrap());
        assert_eq!(
            last_commit_message(&f.root),
            "Lusterna: uncommitted changes"
        );
    }

    #[test]
    fn write_spec_writes_and_commits_the_spec_and_its_properties() {
        let f = fixture(r#"{"entrypoints": ["demo::f"]}"#, &[]);
        f.pipeline.write_spec().unwrap();

        let lusterna_dir = f.root.join("lusterna");
        let spec: Spec =
            serde_json::from_str(&fs::read_to_string(lusterna_dir.join("lusterna.json")).unwrap())
                .unwrap();
        assert_eq!(spec.entrypoints, ["demo::f"]);
        let properties: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(lusterna_dir.join("properties.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(properties, serde_json::json!([]));
        assert_eq!(last_commit_message(&f.root), "Lusterna: spec");

        let head = git::head_rev(&f.root).unwrap();
        f.pipeline.write_spec().unwrap();
        assert_eq!(git::head_rev(&f.root).unwrap(), head);
    }

    #[test]
    fn commit_names_the_stage() {
        let f = fixture("{}", &[]);
        let cases = [
            (StageName::Infer, "infer"),
            (StageName::PreTranslate, "pre-translate"),
            (StageName::Translate, "translate"),
            (StageName::Formalize, "formalize"),
            (StageName::Prove, "prove"),
        ];
        for (stage, name) in cases {
            fs::write(f.root.join(format!("{name}.txt")), "").unwrap();
            f.pipeline.commit(stage).unwrap();
            assert_eq!(
                last_commit_message(&f.root),
                format!("Lusterna: `{name}` stage")
            );
        }
    }

    #[test]
    fn commit_without_changes_makes_no_commit() {
        let f = fixture("{}", &[]);
        let head = git::head_rev(&f.root).unwrap();
        f.pipeline.commit(StageName::Translate).unwrap();
        assert_eq!(git::head_rev(&f.root).unwrap(), head);
    }
}
