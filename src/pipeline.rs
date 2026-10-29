use std::{
    fs::canonicalize,
    path::{Path, PathBuf},
};

use anyhow::Context;
use serde::{Deserialize, Serialize};

use crate::{
    agent::Agent,
    config::Config,
    log::stage_info,
    stage::{
        Defect, Stage, StageExecutionError,
        pre_translate::{PreTranslateChange, PreTranslateStage},
        translate::TranslateStage,
    },
};

pub enum EntryStage {
    Translate {
        entrypoints: Vec<String>,
        opaque: Vec<String>,
    },
}

pub struct PipelineDef {
    workspace_root: PathBuf,
    crate_root: Option<PathBuf>,
    start_at: EntryStage,
    stop_at: String,
}

pub struct Pipeline {
    def: PipelineDef,
}

impl Pipeline {
    pub fn execute(&mut self) {
        todo!()
    }

    fn run(&mut self) {}

    fn continue_pipeline(&mut self) {}
}

fn in_worktree(p: &Path, repo_root: &Path, worktree: &Path) -> anyhow::Result<PathBuf> {
    let p = canonicalize(p)?;
    let relative = p.strip_prefix(&repo_root).with_context(|| {
        format!(
            "`{}` is not inside the repository `{}`",
            p.display(),
            repo_root.display()
        )
    })?;
    Ok(worktree.join(relative))
}
