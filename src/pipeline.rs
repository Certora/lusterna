//! Runs the stages on a Rust crate, as its [`Spec`] says:
//!
//! 1. `infer`: runs only if the spec asks for it. An agent finds properties and
//!    entrypoints in addition to those of the spec. The pipeline writes the
//!    submission of the stage to `lusterna/submissions/infer.json` in the crate
//!    root, and the later steps use its properties and entrypoints.
//! 2. The pipeline writes the spec to `lusterna/lusterna.json` and its
//!    properties to `lusterna/properties.json` in the crate root.
//! 3. `pre-translate`: runs only if `cargo build` fails. An agent changes the
//!    crate so that it builds on the host, without changing what it computes on
//!    its target.
//! 4. `translate`: translates the code reachable from the entrypoints, with the
//!    default opaque patterns and those of the spec passed to Charon. The
//!    pipeline writes the submission of the stage to
//!    `lusterna/submissions/translate.json` in the crate root.
//! 5. `formalize`: states each property as Lean theorems about the translation,
//!    without proofs. Runs only if the spec has properties. The pipeline writes
//!    the submission of the stage to `lusterna/submissions/formalize.json` in
//!    the crate root.
//! 6. `prove`: proves the theorems of the formalize stage, or shows that a
//!    theorem is false. Runs only if the spec has properties. It reads
//!    `lusterna/submissions/formalize.json`, and the pipeline writes the
//!    submission of the stage to `lusterna/submissions/prove.json` in the crate
//!    root.
//! 7. The pipeline writes the report of the run to `lusterna/report.md` in the
//!    crate root, from `translate.json`, `formalize.json` and `prove.json` in
//!    `lusterna/submissions/`. The report shows a stage whose file is missing
//!    as skipped.
//!
//! After each step, the pipeline commits all changes
//! in the repository.
//!
//! ## Partial Runs
//!
//! The `--stop-after` option ends the pipeline after the named
//! [`PipelineStep`]. The report is written even with `--stop-after`.
//!
//! ## Continuing Runs
//!
//! `lusterna continue` goes on with the unfinished run of the crate: a run
//! that was interrupted, that failed, or that `--stop-after` ended before the
//! `prove` step. It starts at the first step that did not finish. A stage that
//! was interrupted goes on from its saved state. `continue` takes its options
//! from its own command line, not from the run it continues. It fails if the
//! crate has no unfinished run or if `HEAD` moved since the run stopped. It
//! does not check for uncommitted changes.
//!
//! The state of a run is in two kinds of files in `lusterna/.internal/` in
//! the crate root, which `lusterna/.gitignore` excludes from the commits:
//!
//! - `run.json`: the spec, the path of the spec file, the commit the run
//!   started from, the step that runs next, `HEAD`, `HEAD` before and after
//!   each step, and whether the run finished. The pipeline writes it at the
//!   start of a run and before and after each step. A run that `--stop-after`
//!   did not end before the `prove` step is finished.
//! - `<stage>.state.json`: the state of the execution of a stage, written
//!   before the stage runs and after each of its phases.
//!
//! A new run deletes the submission files and the state files of the earlier
//! run.
//!
//! ## Resetting Runs
//!
//! `lusterna reset <step>` sets the run of the crate, finished or not, back
//! to the start of a step, and `lusterna continue` then runs that step again.
//! It deletes the commits and the stage states of the later steps. It fails
//! if the run did not start the step.
//!
//! - Without `--full`, the work of the step stays in the work tree as
//!   uncommitted changes on the commit before the step, and the submission of
//!   its stage is deleted. The stage keeps its state file and starts again
//!   with new agent sessions, and its first prompt says that the work tree
//!   holds an earlier attempt. This fails if the state file of the stage does
//!   not load.
//! - With `--full`, the repository is reset to the commit before the step and
//!   the state file of its stage is deleted, so the stage starts from its
//!   setup.
//!
//! The command fails if the reset deletes commits that Lusterna did not make
//! or uncommitted changes, unless `--force` is given and the user confirms.
//! If a step of the run started and did not finish, the uncommitted changes
//! are its work and do not count.

use std::{
    collections::BTreeMap,
    fs, io,
    path::{Path, PathBuf},
    time::Instant,
};

use log::info;
use serde::{Deserialize, Serialize, de::DeserializeOwned};

use crate::{
    agent::AgentError,
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

/// The content of `lusterna/.gitignore` in the crate root.
const GITIGNORE: &str = "/lean/.lake/\n/extraction.llbc\n/charon/\n/.internal/\n";

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
    /// The spec has no entrypoints and does not ask to infer them.
    #[error("the spec has no entrypoints; add some or set `infer` to true")]
    NoEntrypoints,
    /// Writing the spec or the properties to the crate failed.
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    /// A file that holds the submission of a stage does not parse.
    #[error("cannot parse the stage submission in `{0}`: {1}")]
    StageSubmission(PathBuf, serde_json::Error),
    /// The report templates do not render.
    #[error("cannot render the report: {0}")]
    Report(#[from] minijinja::Error),
    /// `lusterna/.internal/run.json` does not exist in the crate root.
    #[error("the crate has no run")]
    NoRun,
    /// The run of the crate finished.
    #[error("the crate has no unfinished run to continue")]
    NoUnfinishedRun,
    /// The file that holds the state of the unfinished run does not parse.
    #[error("cannot parse the run state in `{0}`: {1}")]
    RunState(PathBuf, serde_json::Error),
    /// The unfinished run is past the step of a stage, but the file that
    /// holds the submission of the stage does not exist.
    #[error("the unfinished run has no stage submission in `{0}`; start a new run")]
    MissingSubmission(PathBuf),
    /// `HEAD` of the repository is not the commit that the state of the
    /// unfinished run holds.
    #[error("`HEAD` is at {head}, but the unfinished run stopped at {saved}")]
    HeadMoved { saved: String, head: String },
    /// The state of the run has no commits for the step.
    #[error("the run did not start the step `{0:?}`")]
    StepNotStarted(PipelineStep),
}

/// The steps of a pipeline run, in the order in which they run. A step
/// executes a stage or does other work on the crate, and ends with a commit
/// of its changes.
#[derive(
    Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, clap::ValueEnum, Serialize, Deserialize,
)]
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

impl PipelineStep {
    /// The stage that the step executes, or `None` if it executes no stage.
    fn stage(self) -> Option<StageName> {
        match self {
            PipelineStep::Infer => Some(StageName::Infer),
            PipelineStep::PreTranslate => Some(StageName::PreTranslate),
            PipelineStep::Translate => Some(StageName::Translate),
            PipelineStep::Formalize => Some(StageName::Formalize),
            PipelineStep::Prove => Some(StageName::Prove),
            PipelineStep::WriteSpec | PipelineStep::Report => None,
        }
    }
}

/// `HEAD` of the repository before and after a step.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct StepCommits {
    pub before: String,
    /// `None` if the step did not finish.
    pub after: Option<String>,
}

/// The state of a run.
#[derive(Clone, Serialize, Deserialize)]
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
    /// The commits of each step that started.
    #[serde(default)]
    steps: BTreeMap<PipelineStep, StepCommits>,
    /// Whether the run finished.
    #[serde(default)]
    finished: bool,
}

impl RunState {
    /// Reads the state from `lusterna/.internal/run.json` in the crate root.
    /// Returns `None` if the file does not exist.
    fn read(config: &Config) -> Result<Option<Self>, PipelineError> {
        let path = config.internal_dir()?.join("run.json");
        let text = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e.into()),
        };
        serde_json::from_str(&text)
            .map(Some)
            .map_err(|e| PipelineError::RunState(path, e))
    }

    /// Writes the state to `lusterna/.internal/run.json` in the crate root.
    fn write(&self, config: &Config) -> Result<(), PipelineError> {
        let internal_dir = config.internal_dir()?;
        fs::create_dir_all(&internal_dir)?;
        fs::write(
            internal_dir.join("run.json"),
            serde_json::to_string_pretty(self).expect("the run state serializes"),
        )?;
        Ok(())
    }
}

/// A pipeline of stages, configured by a spec. See the module documentation.
pub struct Pipeline {
    /// The state of the run. Its `head` is `HEAD` of the repository when the
    /// run started, or when the state that the run was loaded from was
    /// written.
    state: RunState,
    config: Config,
    submissions: Submissions,
}

impl Pipeline {
    /// Creates the pipeline for a new run of `spec`, which was read from
    /// `spec_path`. Deletes the submissions and the stage states of an
    /// earlier run.
    pub fn new(spec: Spec, spec_path: PathBuf, config: Config) -> Result<Self, PipelineError> {
        // Delete the state of the earlier run before its submissions, which
        // `resume` expects to be stored
        let _ = fs::remove_file(config.internal_dir()?.join("run.json"));

        let head = git::head_rev(&config.repo_root()?)?;
        let pipeline = Self {
            state: RunState {
                spec,
                spec_path,
                commit: head.clone(),
                next: PipelineStep::Infer,
                head,
                steps: BTreeMap::new(),
                finished: false,
            },
            submissions: Submissions::new(&config.submissions_dir()?)?,
            config,
        };

        let lusterna_dir = pipeline.config.lusterna_dir()?;
        let internal_dir = pipeline.config.internal_dir()?;
        fs::create_dir_all(&internal_dir)?;
        remove_stage_states(&internal_dir)?;
        fs::write(lusterna_dir.join(".gitignore"), GITIGNORE)?;
        pipeline.save_state()?;

        Ok(pipeline)
    }

    /// Creates the pipeline for the run of the crate, finished or not, from
    /// the state in `lusterna/.internal/run.json`, with the stored
    /// submissions.
    pub fn load(config: Config) -> Result<Self, PipelineError> {
        let Some(state) = RunState::read(&config)? else {
            return Err(PipelineError::NoRun);
        };
        Ok(Self {
            state,
            submissions: Submissions::load(&config.submissions_dir()?)?,
            config,
        })
    }

    /// Continues the run from `next`. Fails if the run finished, if `HEAD`
    /// moved since the state of the run was written, or if the submission of
    /// an earlier stage is missing. The repository may have uncommitted
    /// changes.
    pub fn resume(self) -> Result<(), PipelineError> {
        if self.state.finished {
            return Err(PipelineError::NoUnfinishedRun);
        }
        let head = git::head_rev(&self.config.repo_root()?)?;
        if head != self.state.head {
            return Err(PipelineError::HeadMoved {
                saved: self.state.head,
                head,
            });
        }
        self.check_state_consistent()?;
        info!("Continuing the run at `{:?}`", self.state.next);
        self.run()
    }

    /// Checks that the submission of each stage whose step is before `next`
    /// is stored.
    fn check_state_consistent(&self) -> Result<(), PipelineError> {
        let stored = [
            (
                PipelineStep::Infer,
                StageName::Infer,
                // The infer step runs only if the spec asks for it
                !self.state.spec.infer || self.submissions.get_infer().is_some(),
            ),
            (
                PipelineStep::PreTranslate,
                StageName::PreTranslate,
                self.submissions.get_pre_translate().is_some(),
            ),
            (
                PipelineStep::Translate,
                StageName::Translate,
                self.submissions.get_translate().is_some(),
            ),
            (
                PipelineStep::Formalize,
                StageName::Formalize,
                self.submissions.get_formalize().is_some(),
            ),
            (
                PipelineStep::Prove,
                StageName::Prove,
                self.submissions.get_prove().is_some(),
            ),
        ];
        for (step, stage, stored) in stored {
            if step < self.state.next && !stored {
                let dir = self.config.submissions_dir()?;
                return Err(PipelineError::MissingSubmission(
                    Submissions::submission_path(&dir, stage),
                ));
            }
        }
        Ok(())
    }

    /// Whether `step` is `next` or a later step, and runs with
    /// `--stop-after`.
    fn runs(&self, step: PipelineStep) -> bool {
        self.state.next <= step && self.config.stop_after.is_none_or(|s| step <= s)
    }

    /// Writes the state of the run to `lusterna/.internal/run.json` in the
    /// crate root.
    fn save_state(&self) -> Result<(), PipelineError> {
        let state = RunState {
            head: git::head_rev(&self.config.repo_root()?)?,
            ..self.state.clone()
        };
        state.write(&self.config)
    }

    /// Records `HEAD` as the commit before `step`, unless the step started
    /// before, and saves the state of the run.
    fn step_started(&mut self, step: PipelineStep) -> Result<(), PipelineError> {
        if !self.state.steps.contains_key(&step) {
            let commits = StepCommits {
                before: git::head_rev(&self.config.repo_root()?)?,
                after: None,
            };
            self.state.steps.insert(step, commits);
        }
        self.save_state()
    }

    /// Records `HEAD` as the commit after `step`, sets `next` to the step
    /// that runs next and saves the state of the run.
    fn step_done(&mut self, step: PipelineStep, next: PipelineStep) -> Result<(), PipelineError> {
        let head = git::head_rev(&self.config.repo_root()?)?;
        if let Some(commits) = self.state.steps.get_mut(&step) {
            commits.after = Some(head);
        }
        self.state.next = next;
        self.save_state()
    }

    /// Saves the state of the run, as finished unless `--stop-after` ended
    /// the run before the `prove` step.
    fn close_state(&mut self) -> Result<(), PipelineError> {
        match self.config.stop_after {
            Some(step) if step < PipelineStep::Prove => {
                info!("Stopped the run after `{:?}`", step)
            }
            _ => self.state.finished = true,
        }
        self.save_state()
    }

    /// Returns the commits of each step that started.
    pub fn steps(&self) -> &BTreeMap<PipelineStep, StepCommits> {
        &self.state.steps
    }

    /// Resets the run to the commit before `step`, so that
    /// `lusterna continue` runs `step` next. Deletes the commits, the
    /// uncommitted changes and the stage states of the later steps. Fails if
    /// the run did not start `step`.
    ///
    /// With `full`, deletes those of `step` too. Without `full`, keeps the
    /// work of `step` as uncommitted changes, deletes the submission of its
    /// stage, and starts its stage again as `StageExec::restart` says.
    pub fn reset(mut self, step: PipelineStep, full: bool) -> Result<(), PipelineError> {
        let Some(commits) = self.state.steps.get(&step).cloned() else {
            return Err(PipelineError::StepNotStarted(step));
        };
        let repo = self.config.repo_root()?;

        if full {
            git::reset(&repo, &commits.before, true)?;
            // The commit before the first step of a run does not have the file
            let lusterna_dir = self.config.lusterna_dir()?;
            fs::create_dir_all(&lusterna_dir)?;
            fs::write(lusterna_dir.join(".gitignore"), GITIGNORE)?;
        } else {
            // Start the stage again first: if its state file does not load,
            // the repository stays as it is
            match step {
                PipelineStep::Infer => self
                    .restart_stage::<InferStage>()
                    .map_err(PipelineError::Infer)?,
                PipelineStep::PreTranslate => self
                    .restart_stage::<PreTranslateStage>()
                    .map_err(PipelineError::PreTranslate)?,
                PipelineStep::Translate => self
                    .restart_stage::<TranslateStage>()
                    .map_err(PipelineError::Translate)?,
                PipelineStep::Formalize => self
                    .restart_stage::<FormalizeStage>()
                    .map_err(PipelineError::Formalize)?,
                PipelineStep::Prove => self
                    .restart_stage::<ProveStage>()
                    .map_err(PipelineError::Prove)?,
                PipelineStep::WriteSpec | PipelineStep::Report => {}
            }

            // Delete the work of the later steps
            if let Some(after) = &commits.after {
                git::reset(&repo, after, true)?;
            }
            // Keep the work of the step as uncommitted changes
            git::reset(&repo, &commits.before, false)?;
            if let Some(stage) = step.stage() {
                let dir = self.config.submissions_dir()?;
                remove_if_exists(&Submissions::submission_path(&dir, stage))?;
            }
            let unfinished = StepCommits {
                before: commits.before,
                after: None,
            };
            self.state.steps.insert(step, unfinished);
        }

        // Forget the later steps that started, and `step` too with `full`
        let started: Vec<PipelineStep> = self.state.steps.keys().copied().collect();
        for later in started {
            if later < step || (later == step && !full) {
                continue;
            }
            self.state.steps.remove(&later);
            if let Some(stage) = later.stage() {
                remove_if_exists(&self.stage_state_path(stage)?)?;
            }
        }

        self.state.next = step;
        self.state.finished = false;
        self.save_state()?;
        info!("Reset the run to `{:?}`", step);
        Ok(())
    }

    /// Replaces the execution in the state file of the stage `S` with the
    /// one that `StageExec::restart` returns for it.
    fn restart_stage<S: Stage>(&self) -> Result<(), StageExecutionError<S>> {
        let path = self.stage_state_path(S::NAME).map_err(AgentError::from)?;
        let exec = StageExec::<S>::load(&path, &self.config)?.restart(&self.config)?;
        let state = serde_json::to_string(&exec).expect("the stage state serializes");
        fs::write(&path, state).map_err(|e| StageExecutionError::StateFile(path, e))
    }

    /// Runs the steps of the pipeline from `next` on, in order, and stops at
    /// the first error.
    pub fn run(mut self) -> Result<(), PipelineError> {
        // 1. Infer the entrypoints and properties
        if self.state.spec.infer {
            if self.runs(PipelineStep::Infer) {
                self.step_started(PipelineStep::Infer)?;
                let submission = self.run_infer()?;
                self.submissions.set_infer(submission)?;
                self.commit(StageName::Infer)?;
                self.step_done(PipelineStep::Infer, PipelineStep::WriteSpec)?;
            }
            // The later steps use the inferred properties and entrypoints.
            let inferred = self
                .submissions
                .get_infer()
                .expect("the infer step stored the submission");
            self.state.spec.properties = inferred.properties.clone();
            self.state.spec.entrypoints = inferred.entrypoints.clone();
        }

        // 2. Write the spec and its properties to the crate
        if self.runs(PipelineStep::WriteSpec) {
            self.step_started(PipelineStep::WriteSpec)?;
            self.write_spec()?;
            self.step_done(PipelineStep::WriteSpec, PipelineStep::PreTranslate)?;
        }

        // 3. Run the pre-translate stage
        // this is a noop if the project does build.
        if self.runs(PipelineStep::PreTranslate) {
            self.step_started(PipelineStep::PreTranslate)?;
            let submission = self.run_pretranslate()?;
            self.submissions.set_pre_translate(submission)?;
            self.commit(StageName::PreTranslate)?;
            self.step_done(PipelineStep::PreTranslate, PipelineStep::Translate)?;
        }

        // 4. Run the translate stage
        if self.runs(PipelineStep::Translate) {
            self.step_started(PipelineStep::Translate)?;
            if self.state.spec.entrypoints.is_empty() {
                return Err(PipelineError::NoEntrypoints);
            }
            let submission = self.run_translate(
                self.state.spec.entrypoints.clone(),
                opaque_patterns(&self.config, &self.state.spec.add_opaque)?,
            )?;
            self.submissions.set_translate(submission)?;
            self.commit(StageName::Translate)?;
            self.step_done(PipelineStep::Translate, PipelineStep::Formalize)?;
        }

        // 5. Run the formalize stage
        if self.runs(PipelineStep::Formalize) && !self.state.spec.properties.0.is_empty() {
            self.step_started(PipelineStep::Formalize)?;
            let submission = self.run_formalize()?;
            self.submissions.set_formalize(submission)?;
            self.commit(StageName::Formalize)?;
            self.step_done(PipelineStep::Formalize, PipelineStep::Prove)?;
        }

        // 6. Run the prove stage
        if self.runs(PipelineStep::Prove) && !self.state.spec.properties.0.is_empty() {
            self.step_started(PipelineStep::Prove)?;
            let submission = self.run_prove()?;
            self.submissions.set_prove(submission)?;
            self.commit(StageName::Prove)?;
            self.step_done(PipelineStep::Prove, PipelineStep::Report)?;
        }

        // 7. Write the report (always)
        self.write_report()?;
        self.close_state()
    }

    fn write_spec(&self) -> Result<(), PipelineError> {
        let lusterna_dir = self.config.lusterna_dir()?;
        fs::create_dir_all(&lusterna_dir)?;
        fs::write(
            lusterna_dir.join("lusterna.json"),
            serde_json::to_string_pretty(&self.state.spec).expect("the spec serializes"),
        )?;
        fs::write(
            lusterna_dir.join("properties.json"),
            serde_json::to_string_pretty(&self.state.spec.properties)
                .expect("the properties serialize"),
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
        // A stage that a run stopped in the middle of continues even if the
        // crate builds: the gate and the judge did not check its changes.
        let unfinished = self.stage_state_path(StageName::PreTranslate)?.exists();
        let build_stderr = match cargo::build(&manifest) {
            Ok(_) if !unfinished => return Ok(PreTranslateSubmission::default()),
            Ok(_) => String::new(),
            Err(CargoError::Build { stderr, .. }) => {
                info!("Crate does not build, executing pre translation stage");
                stderr
            }
            Err(e) => return Err(PipelineError::Cargo(e)),
        };

        let input = PreTranslateInput {
            workspace_root: self.config.workspace_root()?,
            manifest: manifest.to_owned(),
            build_stderr,
        };
        let submission = self
            .exec_stage::<PreTranslateStage>(input)
            .map_err(PipelineError::PreTranslate)?;

        Ok(submission)
    }

    /// Returns the file that holds the state of the execution of `stage`.
    fn stage_state_path(&self, stage: StageName) -> Result<PathBuf, ConfigError> {
        let name = format!("{}.state.json", stage.as_str());
        Ok(self.config.internal_dir()?.join(name))
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
            existing_properties: self.state.spec.properties.clone(),
            existing_entrypoints: self.state.spec.entrypoints.clone(),
            design_doc: self.state.spec.design_doc.clone(),
            platform: self.config.get_platform()?,
        };
        let submission = self
            .exec_stage::<InferStage>(input)
            .map_err(PipelineError::Infer)?;

        Ok(submission)
    }

    fn run_formalize(&self) -> Result<FormalizeSubmission, PipelineError> {
        let lusterna_dir = self.config.lusterna_dir()?;
        let lean_root = lusterna_dir.join("lean");
        let input = FormalizeInput {
            extracted: lean_root.join("Extraction"),
            lean_root,
            properties: lusterna_dir.join("properties.json"),
            crate_root: self.config.crate_root()?,
            entrypoints: self.state.spec.entrypoints.clone(),
        };
        let submission = self
            .exec_stage::<FormalizeStage>(input)
            .map_err(PipelineError::Formalize)?;

        Ok(submission)
    }

    fn run_prove(&self) -> Result<ProveSubmission, PipelineError> {
        let lusterna_dir = self.config.lusterna_dir()?;
        let lean_root = lusterna_dir.join("lean");

        let formalized = self
            .submissions
            .get_formalize()
            .expect("the formalize step stored the submission");

        let input = ProveInput {
            extracted: lean_root.join("Extraction"),
            lean_root,
            properties: lusterna_dir.join("properties.json"),
            crate_root: self.config.crate_root()?,
            formalized: formalized_properties(
                &self.state.spec.properties,
                formalized.property_status.clone(),
            ),
            induction: formalized.induction.clone(),
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
        let lusterna_dir = self.config.lusterna_dir()?;
        fs::create_dir_all(&lusterna_dir)?;

        // Submissions
        let pre_translate = self.submissions.get_pre_translate();
        let translate = self.submissions.get_translate();
        let formalize = self.submissions.get_formalize();
        let prove = self.submissions.get_prove();

        let report = report::render(
            cargo::package_name(&self.config.manifest()?)?,
            &self.state.commit,
            &self.config.aeneas_rev()?,
            &self.state.spec.entrypoints,
            &self.state.spec.properties,
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
        let state_path = self.stage_state_path(S::NAME).map_err(AgentError::from)?;

        // Continue the stage if a run stopped in the middle of it
        let stage_exec = if state_path.exists() {
            log::info!("Continuing `{}`", S::NAME);
            StageExec::load(&state_path, &self.config)?
        } else {
            let stage = S::setup(input).map_err(StageExecutionError::SetupError)?;
            StageExec::new(stage, &self.config)?
        };
        let t = Instant::now();
        log::info!("Executing `{}`", S::NAME);

        // Saves the state of the stage and of the run
        let mut save = |exec: &StageExec<S>| {
            let state = serde_json::to_string(exec).expect("the stage state serializes");
            let saved = fs::write(&state_path, state)
                .map_err(PipelineError::from)
                .and_then(|()| self.save_state());
            if let Err(e) = saved {
                log::warn!("Cannot save the state of `{}`: {}", S::NAME, e);
            }
        };
        save(&stage_exec);
        let submission = stage_exec.run(&self.config, Some(&mut save))?;
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
    /// Creates the submissions of `dir`. Deletes `dir` with its content and
    /// creates it again.
    pub fn new(dir: &Path) -> io::Result<Self> {
        match fs::remove_dir_all(dir) {
            Err(e) if e.kind() != io::ErrorKind::NotFound => return Err(e),
            _ => {}
        }
        fs::create_dir_all(dir)?;
        Ok(Self {
            infer: None,
            pre_translate: None,
            translate: None,
            formalize: None,
            prove: None,
            submission_dir: dir.to_path_buf(),
        })
    }

    /// Reads the submissions from the submission files in `dir`.
    pub fn load(dir: &Path) -> io::Result<Self> {
        Ok(Self {
            infer: Self::read_submission(&Self::submission_path(dir, StageName::Infer))?,
            pre_translate: Self::read_submission(&Self::submission_path(
                dir,
                StageName::PreTranslate,
            ))?,
            translate: Self::read_submission(&Self::submission_path(dir, StageName::Translate))?,
            formalize: Self::read_submission(&Self::submission_path(dir, StageName::Formalize))?,
            prove: Self::read_submission(&Self::submission_path(dir, StageName::Prove))?,
            submission_dir: dir.to_path_buf(),
        })
    }

    pub fn set_infer(&mut self, submission: InferStageSubmission) -> io::Result<()> {
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
        fs::create_dir_all(&self.submission_dir)?;
        fs::write(
            path,
            serde_json::to_string_pretty(&submission).expect("the formalize submission serializes"),
        )
    }

    fn read_submission<T: DeserializeOwned>(path: &Path) -> io::Result<Option<T>> {
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

/// Deletes the file `path` if it exists.
fn remove_if_exists(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(e) if e.kind() != io::ErrorKind::NotFound => Err(e),
        _ => Ok(()),
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
    use crate::utils::crate_repo;

    struct Fixture {
        pipeline: Pipeline,
        /// The root of the repository, which holds the crate
        root: PathBuf,
        _dir: TempDir,
    }

    /// A pipeline for a crate at the root of a new repository with one commit,
    /// with the spec `spec` and the command-line arguments `args`
    fn fixture(spec: &str, args: &[&str]) -> Fixture {
        let (dir, root, config) = crate_repo(spec, args);

        Fixture {
            // Directly construct the Pipeline without calling "new",
            // which writes to `lusterna/` in the crate root.
            pipeline: Pipeline {
                state: RunState {
                    spec: serde_json::from_str(spec).unwrap(),
                    spec_path: root.join("lusterna.json"),
                    commit: "start".to_owned(),
                    next: PipelineStep::Infer,
                    head: "start".to_owned(),
                    steps: BTreeMap::new(),
                    finished: false,
                },
                submissions: Submissions {
                    infer: None,
                    pre_translate: None,
                    translate: None,
                    formalize: None,
                    prove: None,
                    submission_dir: config.submissions_dir().unwrap(),
                },
                config,
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
        f.pipeline.state.next = PipelineStep::Translate;

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
        f.pipeline.state.next = PipelineStep::Translate;
        f.pipeline.save_state().unwrap();

        let run_state = f.pipeline.config.internal_dir().unwrap().join("run.json");
        let text = fs::read_to_string(run_state).unwrap();
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
        let before = git::head_rev(&f.root).unwrap();
        f.pipeline.step_started(PipelineStep::Translate).unwrap();
        fs::write(f.root.join("new.rs"), "").unwrap();
        git::commit_all(&f.root, "work").unwrap();

        f.pipeline
            .step_done(PipelineStep::Translate, PipelineStep::Formalize)
            .unwrap();

        assert!(f.pipeline.state.next == PipelineStep::Formalize);
        let run_state = f.pipeline.config.internal_dir().unwrap().join("run.json");
        let text = fs::read_to_string(run_state).unwrap();
        let state: RunState = serde_json::from_str(&text).unwrap();
        assert!(state.next == PipelineStep::Formalize);
        let expected = StepCommits {
            before,
            after: Some(git::head_rev(&f.root).unwrap()),
        };
        assert_eq!(state.steps[&PipelineStep::Translate], expected);
    }

    #[test]
    fn step_started_keeps_the_commit_of_a_step_that_started_before() {
        let mut f = fixture("{}", &[]);
        let before = git::head_rev(&f.root).unwrap();
        f.pipeline.step_started(PipelineStep::Translate).unwrap();
        fs::write(f.root.join("new.rs"), "").unwrap();
        git::commit_all(&f.root, "work").unwrap();

        f.pipeline.step_started(PipelineStep::Translate).unwrap();
        let run_state = f.pipeline.config.internal_dir().unwrap().join("run.json");
        let text = fs::read_to_string(run_state).unwrap();
        let state: RunState = serde_json::from_str(&text).unwrap();
        let expected = StepCommits {
            before,
            after: None,
        };
        assert_eq!(state.steps[&PipelineStep::Translate], expected);
    }

    #[test]
    fn close_state_saves_the_state_of_a_finished_run_as_finished() {
        for args in [
            &[][..],
            &["--stop-after", "prove"],
            &["--stop-after", "report"],
        ] {
            let mut f = fixture("{}", args);

            f.pipeline.close_state().unwrap();
            let run_state = f.pipeline.config.internal_dir().unwrap().join("run.json");
            let text = fs::read_to_string(run_state).unwrap();
            let state: RunState = serde_json::from_str(&text).unwrap();
            assert!(state.finished, "{args:?}");
        }
    }

    #[test]
    fn resume_of_a_finished_run_fails() {
        let mut f = fixture("{}", &[]);
        f.pipeline.close_state().unwrap();

        let loaded = Pipeline::load(f.pipeline.config).unwrap();
        let error = loaded.resume().unwrap_err();
        assert!(matches!(error, PipelineError::NoUnfinishedRun));
    }

    #[test]
    fn close_state_saves_the_state_of_a_run_that_stopped_before_prove() {
        let mut f = fixture("{}", &["--stop-after", "formalize"]);
        f.pipeline.state.next = PipelineStep::Prove;
        f.pipeline.save_state().unwrap();
        fs::write(f.root.join("report.md"), "").unwrap();
        git::commit_all(&f.root, "Lusterna: report").unwrap();

        f.pipeline.close_state().unwrap();
        let run_state = f.pipeline.config.internal_dir().unwrap().join("run.json");
        let text = fs::read_to_string(run_state).unwrap();
        let state: RunState = serde_json::from_str(&text).unwrap();
        assert!(state.next == PipelineStep::Prove);
        assert!(!state.finished);
        assert_eq!(state.head, git::head_rev(&f.root).unwrap());
    }

    #[test]
    fn new_deletes_the_content_of_the_submission_dir() {
        let dir = tempfile::tempdir().unwrap();
        let submission_dir = dir.path().join("submissions");
        let mut submissions = Submissions::new(&submission_dir).unwrap();
        submissions
            .set_pre_translate(PreTranslateSubmission::default())
            .unwrap();
        fs::write(submission_dir.join("prove.json"), "stale").unwrap();

        let submissions = Submissions::new(&submission_dir).unwrap();
        assert!(submissions.get_pre_translate().is_none());
        assert!(submission_dir.read_dir().unwrap().next().is_none());
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

    fn pre_translate_input(f: &Fixture) -> PreTranslateInput {
        PreTranslateInput {
            workspace_root: f.root.clone(),
            manifest: f.root.join("Cargo.toml"),
            build_stderr: String::new(),
        }
    }

    /// Writes the state file of a pre-translate stage that is done, with a
    /// submission of one change, and returns its path
    fn write_done_pre_translate_state(f: &Fixture) -> PathBuf {
        let stage = PreTranslateStage::setup(pre_translate_input(f)).unwrap();
        let exec = StageExec::new(stage, &f.pipeline.config).unwrap();
        let mut state = serde_json::to_value(&exec).unwrap();
        let change = serde_json::json!({
            "file": "src/lib.rs", "change": "change", "reason": "reason"
        });
        state["phase"] = serde_json::json!({"Done": {"changes": [change]}});
        let internal_dir = f.pipeline.config.internal_dir().unwrap();
        let state_path = internal_dir.join("pre-translate.state.json");
        fs::create_dir_all(&internal_dir).unwrap();
        fs::write(&state_path, state.to_string()).unwrap();
        state_path
    }

    #[test]
    fn exec_stage_returns_the_submission_of_a_done_state_file() {
        let f = fixture("{}", &[]);
        let state_path = write_done_pre_translate_state(&f);

        let input = pre_translate_input(&f);
        let submission = f.pipeline.exec_stage::<PreTranslateStage>(input).unwrap();
        assert_eq!(submission.changes.len(), 1);
        assert!(state_path.exists());
        assert!(
            f.pipeline
                .config
                .internal_dir()
                .unwrap()
                .join("run.json")
                .exists()
        );
    }

    #[test]
    fn load_without_run_state_fails() {
        let f = fixture("{}", &[]);
        let error = Pipeline::load(f.pipeline.config).err().unwrap();
        assert!(matches!(error, PipelineError::NoRun));
    }

    #[test]
    fn load_with_a_malformed_run_state_fails() {
        let f = fixture("{}", &[]);
        let internal_dir = f.pipeline.config.internal_dir().unwrap();
        let state_path = internal_dir.join("run.json");
        fs::create_dir_all(&internal_dir).unwrap();
        fs::write(&state_path, "not json").unwrap();

        let error = Pipeline::load(f.pipeline.config).err().unwrap();
        assert!(matches!(error, PipelineError::RunState(path, _) if path == state_path));
    }

    #[test]
    fn resume_after_head_moved_fails_with_both_commits() {
        let f = fixture("{}", &[]);
        f.pipeline.save_state().unwrap();
        let saved_head = git::head_rev(&f.root).unwrap();
        fs::write(f.root.join("new.rs"), "").unwrap();
        git::commit_all(&f.root, "moved").unwrap();
        let new_head = git::head_rev(&f.root).unwrap();

        let loaded = Pipeline::load(f.pipeline.config).unwrap();
        let error = loaded.resume().unwrap_err();
        assert!(matches!(
            &error,
            PipelineError::HeadMoved { saved, head } if *saved == saved_head && *head == new_head
        ));
        let message = error.to_string();
        assert!(message.contains(&saved_head) && message.contains(&new_head));
    }

    #[test]
    fn resume_without_the_submission_of_a_step_before_next_fails() {
        let mut f = fixture("{}", &[]);
        f.pipeline.state.next = PipelineStep::Translate;
        f.pipeline.save_state().unwrap();

        let loaded = Pipeline::load(f.pipeline.config).unwrap();
        let error = loaded.resume().unwrap_err();
        let missing = f.root.join("lusterna/submissions/pretranslate.json");
        assert!(matches!(error, PipelineError::MissingSubmission(path) if path == missing));
    }

    #[test]
    fn load_restores_the_run_state_and_the_submissions() {
        let mut f = fixture(r#"{"entrypoints": ["demo::f"]}"#, &[]);
        fs::create_dir_all(f.root.join("lusterna/submissions")).unwrap();
        f.pipeline
            .submissions
            .set_pre_translate(PreTranslateSubmission::default())
            .unwrap();
        f.pipeline.state.next = PipelineStep::Translate;
        f.pipeline.save_state().unwrap();

        let loaded = Pipeline::load(f.pipeline.config).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(loaded.state.spec.entrypoints, ["demo::f"]);
        assert_eq!(loaded.state.spec_path, f.root.join("lusterna.json"));
        assert_eq!(loaded.state.commit, "start");
        assert!(loaded.state.next == PipelineStep::Translate);
        assert!(loaded.submissions.get_pre_translate().is_some());
    }

    #[test]
    fn check_state_consistent_fails_for_a_missing_submission_of_a_step_before_next() {
        let mut f = fixture("{}", &[]);
        f.pipeline.check_state_consistent().unwrap();

        f.pipeline.state.next = PipelineStep::Translate;
        let error = f.pipeline.check_state_consistent().unwrap_err();
        let missing = f.root.join("lusterna/submissions/pretranslate.json");
        assert!(matches!(error, PipelineError::MissingSubmission(path) if path == missing));

        fs::create_dir_all(f.root.join("lusterna/submissions")).unwrap();
        f.pipeline
            .submissions
            .set_pre_translate(PreTranslateSubmission::default())
            .unwrap();
        f.pipeline.check_state_consistent().unwrap();
    }

    #[test]
    fn stage_state_files_are_named_after_their_stage() {
        let f = fixture("{}", &[]);
        let cases = [
            (StageName::Infer, "infer.state.json"),
            (StageName::PreTranslate, "pre-translate.state.json"),
            (StageName::Translate, "translate.state.json"),
            (StageName::Formalize, "formalize.state.json"),
            (StageName::Prove, "prove.state.json"),
        ];
        for (stage, file) in cases {
            assert_eq!(
                f.pipeline.stage_state_path(stage).unwrap(),
                f.root.join("lusterna/.internal").join(file)
            );
        }
    }

    #[test]
    fn run_pretranslate_skips_the_stage_if_the_crate_builds() {
        let f = fixture("{}", &[]);
        let submission = f.pipeline.run_pretranslate().unwrap();
        assert!(submission.changes.is_empty());
        assert!(!f.root.join("lusterna").exists());
    }

    #[test]
    fn run_pretranslate_continues_an_unfinished_stage_even_if_the_crate_builds() {
        let f = fixture("{}", &[]);
        write_done_pre_translate_state(&f);

        let submission = f.pipeline.run_pretranslate().unwrap();
        assert_eq!(submission.changes.len(), 1);
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
    fn load_reads_the_stored_submissions() {
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

        let loaded = Submissions::load(&lusterna_dir).unwrap();
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
    fn set_creates_the_submission_dir() {
        let dir = tempfile::tempdir().unwrap();
        let submission_dir = dir.path().join("submissions");

        let mut submissions = Submissions::load(&submission_dir).unwrap();
        submissions
            .set_pre_translate(PreTranslateSubmission::default())
            .unwrap();
        let loaded = Submissions::load(&submission_dir).unwrap();
        assert!(loaded.get_pre_translate().is_some());
    }

    #[test]
    fn malformed_submission_file_fails_to_load() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("translate.json"), "not json").unwrap();

        let error = Submissions::load(dir.path()).err().unwrap();
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

    /// A finished run whose `pre-translate` and `translate` steps each
    /// committed a file and stored the state of their stage. Returns `HEAD`
    /// before and after the `pre-translate` step.
    fn finished_run() -> (Fixture, String, String) {
        let mut f = fixture("{}", &[]);
        fs::create_dir_all(f.root.join("lusterna/submissions")).unwrap();
        fs::write(f.root.join("lusterna/.gitignore"), GITIGNORE).unwrap();
        git::commit_all(&f.root, "ignore").unwrap();
        let before = git::head_rev(&f.root).unwrap();

        f.pipeline.step_started(PipelineStep::PreTranslate).unwrap();
        write_done_pre_translate_state(&f);
        fs::write(f.root.join("src/lib.rs"), "pub fn f() {}").unwrap();
        f.pipeline
            .submissions
            .set_pre_translate(PreTranslateSubmission::default())
            .unwrap();
        f.pipeline.commit(StageName::PreTranslate).unwrap();
        f.pipeline
            .step_done(PipelineStep::PreTranslate, PipelineStep::Translate)
            .unwrap();
        let after = git::head_rev(&f.root).unwrap();

        f.pipeline.step_started(PipelineStep::Translate).unwrap();
        fs::write(state_file(&f.root, "translate.state.json"), "{}").unwrap();
        fs::write(f.root.join("lusterna/extract.sh"), "").unwrap();
        f.pipeline.commit(StageName::Translate).unwrap();
        f.pipeline
            .step_done(PipelineStep::Translate, PipelineStep::Formalize)
            .unwrap();
        f.pipeline.close_state().unwrap();
        (f, before, after)
    }

    /// The file `name` in `lusterna/.internal` of the crate at `root`
    fn state_file(root: &Path, name: &str) -> PathBuf {
        root.join("lusterna/.internal").join(name)
    }

    fn read_run_state(root: &Path) -> RunState {
        let text = fs::read_to_string(state_file(root, "run.json")).unwrap();
        serde_json::from_str(&text).unwrap()
    }

    #[test]
    fn load_loads_a_finished_run() {
        let (f, _, _) = finished_run();
        let pipeline = Pipeline::load(f.pipeline.config).unwrap_or_else(|e| panic!("{e}"));
        assert!(pipeline.state.finished);
        assert!(pipeline.state.next == PipelineStep::Formalize);
        assert_eq!(pipeline.state.steps.len(), 2);
    }

    #[test]
    fn reset_keeps_the_work_of_the_step_as_uncommitted_changes() {
        let (f, before, _) = finished_run();
        f.pipeline.reset(PipelineStep::PreTranslate, false).unwrap();

        // The work of the step is uncommitted, its submission and the work
        // of the later step are deleted
        assert_eq!(git::head_rev(&f.root).unwrap(), before);
        assert_eq!(
            git::uncommitted_files(&f.root).unwrap(),
            [PathBuf::from("src/lib.rs")]
        );
        assert!(!state_file(&f.root, "translate.state.json").exists());

        // The stage of the step starts again
        let state_path = state_file(&f.root, "pre-translate.state.json");
        let stage_state: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(state_path).unwrap()).unwrap();
        assert!(stage_state["phase"]["Attempt"].is_object());

        let state = read_run_state(&f.root);
        assert!(state.next == PipelineStep::PreTranslate);
        assert!(!state.finished);
        let unfinished = StepCommits {
            before,
            after: None,
        };
        assert_eq!(
            state.steps,
            BTreeMap::from([(PipelineStep::PreTranslate, unfinished)])
        );
    }

    #[test]
    fn reset_without_a_stage_state_that_loads_fails_and_keeps_the_repository() {
        let (f, _, _) = finished_run();
        let head = git::head_rev(&f.root).unwrap();

        let error = f
            .pipeline
            .reset(PipelineStep::Translate, false)
            .unwrap_err();
        let state_path = state_file(&f.root, "translate.state.json");
        assert!(matches!(
            error,
            PipelineError::Translate(StageExecutionError::StateFile(path, _)) if path == state_path
        ));
        assert_eq!(git::head_rev(&f.root).unwrap(), head);
        assert!(read_run_state(&f.root).finished);
    }

    #[test]
    fn full_reset_deletes_the_work_of_the_step_and_of_the_later_steps() {
        let (f, before, _) = finished_run();
        f.pipeline.reset(PipelineStep::PreTranslate, true).unwrap();

        assert_eq!(git::head_rev(&f.root).unwrap(), before);
        assert!(git::is_clean(&f.root).unwrap());
        assert!(!state_file(&f.root, "pre-translate.state.json").exists());
        assert!(!state_file(&f.root, "translate.state.json").exists());

        let state = read_run_state(&f.root);
        assert!(state.next == PipelineStep::PreTranslate);
        assert!(!state.finished);
        assert_eq!(state.head, before);
        assert!(state.steps.is_empty());
    }

    #[test]
    fn reset_keeps_the_earlier_steps() {
        let (f, before, after) = finished_run();
        f.pipeline.reset(PipelineStep::Translate, true).unwrap();

        assert_eq!(git::head_rev(&f.root).unwrap(), after);
        assert!(state_file(&f.root, "pre-translate.state.json").exists());
        let finished = StepCommits {
            before,
            after: Some(after),
        };
        assert_eq!(
            read_run_state(&f.root).steps,
            BTreeMap::from([(PipelineStep::PreTranslate, finished)])
        );
    }

    #[test]
    fn reset_fails_for_a_step_that_did_not_start() {
        let (f, _, _) = finished_run();
        let error = f.pipeline.reset(PipelineStep::Prove, true).unwrap_err();
        assert!(matches!(
            error,
            PipelineError::StepNotStarted(PipelineStep::Prove)
        ));
    }

    #[test]
    fn reset_of_the_first_step_keeps_the_gitignore_and_the_run_state() {
        let mut f = fixture("{}", &[]);
        let before = git::head_rev(&f.root).unwrap();
        fs::create_dir_all(f.root.join("lusterna")).unwrap();
        fs::write(f.root.join("lusterna/.gitignore"), GITIGNORE).unwrap();
        f.pipeline.step_started(PipelineStep::WriteSpec).unwrap();
        f.pipeline.write_spec().unwrap();
        f.pipeline
            .step_done(PipelineStep::WriteSpec, PipelineStep::PreTranslate)
            .unwrap();
        f.pipeline.close_state().unwrap();

        f.pipeline.reset(PipelineStep::WriteSpec, true).unwrap();
        assert_eq!(git::head_rev(&f.root).unwrap(), before);
        let gitignore = fs::read_to_string(f.root.join("lusterna/.gitignore")).unwrap();
        assert_eq!(gitignore, GITIGNORE);
        assert!(read_run_state(&f.root).next == PipelineStep::WriteSpec);
    }
}
