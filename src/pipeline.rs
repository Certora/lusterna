use std::{
    path::{Path, PathBuf},
    time::Instant,
};

use log::{info, warn};

use crate::{
    config::{Config, ConfigError},
    stage::{
        Stage, StageName,
        exec::{StageExec, StageExecutionError},
        pre_translate::{PreTranslateInput, PreTranslateStage},
        translate::{TranslateInput, TranslateOutput, TranslateStage},
    },
    utils::{
        cargo::{self, CargoError},
        format_elapsed,
        git::{self, GitError},
    },
};

enum PipelineKind {
    Translate {
        entrypoints: Vec<String>,
        opaques: Vec<String>,
    },
    #[allow(unused)]
    Verify {
        properties: PathBuf,
        entrypoints: Vec<String>,
        opaques: Vec<String>,
    },
    #[allow(unused)]
    InferAndVerify,
}

#[derive(Debug, thiserror::Error)]
pub enum PipelineError {
    #[error("Error in PreTranslate stage: {0}")]
    PreTranslate(StageExecutionError<PreTranslateStage>),
    #[error("Error in Translate stage: {0}")]
    Translate(StageExecutionError<TranslateStage>),
    #[error(transparent)]
    Git(#[from] GitError),
    #[error(transparent)]
    Config(#[from] ConfigError),
    #[error(transparent)]
    Cargo(#[from] CargoError),
}

pub struct Pipeline {
    kind: PipelineKind,
    config: Config,
    stop_after: Option<StageName>,
}

impl Pipeline {
    pub fn translate(entrypoints: Vec<String>, opaques: Vec<String>, config: Config) -> Self {
        let stop_after = match &config.stop_after {
            Some(sa) => match StageName::try_from(sa.as_str()) {
                Ok(sa) => Some(sa),
                Err(_) => {
                    warn!("Invalid name for --stop-after: '{}', skipping", sa);
                    None
                }
            },
            None => None,
        };
        Self {
            kind: PipelineKind::Translate {
                entrypoints,
                opaques,
            },
            config,
            stop_after,
        }
    }

    pub fn run(self) -> Result<(), PipelineError> {
        match &self.kind {
            PipelineKind::Translate {
                entrypoints,
                opaques,
            } => self.run_translate(entrypoints.clone(), opaques.clone()),
            PipelineKind::Verify { .. } => todo!(),
            PipelineKind::InferAndVerify => todo!(),
        }
    }

    fn run_translate(
        &self,
        entrypoints: Vec<String>,
        opaque: Vec<String>,
    ) -> Result<(), PipelineError> {
        // 1. Run the pre-translate stage if the project does not build
        let manifest = self.config.manifest()?;
        match cargo::build(&manifest) {
            Ok(_) => (),
            Err(CargoError::Build { stderr, .. }) => {
                info!("Crate does not build, executing pre translation stage");
                self.exec_pre_translate_stage(stderr, &manifest)?;
                match git::commit_all(&self.config.repo_root()?, "Lusterna: `pre-translate` stage")?
                {
                    Some(sha) => info!("Committed `pre-translate` work in {}", sha),
                    None => info!("No `pre-translate` work to commit"),
                }
            }
            Err(e) => return Err(PipelineError::Cargo(e)),
        }
        if matches!(&self.stop_after, Some(StageName::PreTranslate)) {
            return Ok(());
        }

        // 2. Run the translate stage
        let _output = self.exec_translate_stage(entrypoints, opaque)?;
        match git::commit_all(&self.config.repo_root()?, "Lusterna: `translate` stage")? {
            Some(sha) => info!("Committed `translate` work in {}", sha),
            None => info!("No `translate` work to commit"),
        }

        Ok(())
    }

    fn exec_pre_translate_stage(
        &self,
        stderr: String,
        manifest: &Path,
    ) -> Result<(), PipelineError> {
        let input = PreTranslateInput {
            workspace_root: self.config.workspace_root()?,
            manifest: manifest.to_owned(),
            build_stderr: stderr,
        };
        self.exec_stage::<PreTranslateStage>(input, &self.config.workspace_root()?)
            .map_err(PipelineError::PreTranslate)
    }

    fn exec_translate_stage(
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
        self.exec_stage::<TranslateStage>(input, &self.config.workspace_root()?)
            .map_err(PipelineError::Translate)
    }

    fn exec_stage<S: Stage>(
        &self,
        input: S::Input,
        workspace_root: &Path,
    ) -> Result<S::Output, StageExecutionError<S>> {
        let stage = S::setup(&input).map_err(StageExecutionError::SetupError)?;
        let t = Instant::now();
        log::info!("Executing `{}`", S::NAME);
        let stage_exec = StageExec::new(stage);
        let output = stage_exec.run(&self.config, workspace_root, None)?;
        log::info!("Executed `{}` in {}", S::NAME, format_elapsed(t));
        Ok(output)
    }
}
