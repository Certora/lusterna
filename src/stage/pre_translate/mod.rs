use std::{fs, path::PathBuf};

use toml_edit::DocumentMut;

use crate::{
    stage::{GateStop, Stage},
    utils::{
        cargo::{self, CargoError},
        git::{self, GitError},
    },
};

/// The agent briefing of the pre-translate stage, with `{workspace_root}` and
/// `{manifest}` standing for the workspace root and the crate's `Cargo.toml`.
const BRIEFING_TEMPLATE: &str = include_str!("res/briefing.md");

/// The first prompt of the pre-translate stage, with `{manifest}` and
/// `{stderr}` standing for the crate's `Cargo.toml` and the stderr of the
/// failed `cargo build`.
const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

/// The `SKILL.md` of the skill `solana-host-build`.
const SOLANA_HOST_BUILD_SKILL: &str = include_str!("res/solana-host-build.md");

pub struct PreTranslateInput {
    /// Absolute path to the workspace of the Rust project.
    pub workspace_root: PathBuf,
    /// Absolute path to the `Cargo.toml` of the crate.
    pub manifest: PathBuf,
    /// The stderr of the failed `cargo build` of the crate.
    pub build_stderr: String,
}

#[derive(Debug, thiserror::Error)]
pub enum PreTranslateGateError {
    #[error("Error running git: {0}")]
    Git(#[from] GitError),
    #[error("Cargo error: {0}")]
    Cargo(#[from] CargoError),
}

#[derive(Debug, derive_more::Display)]
pub enum PreTranslateGateFailure {
    #[display("{} tracked files changed", files.len())]
    ChangedFiles {
        files: Vec<PathBuf>,
        base_commit: String,
    },
    #[display("cargo build failed")]
    CargoBuildFailed { stderr: String },
}

/// Returns whether `old` and `new` are TOML documents that are equal without
/// their `patch` table.
fn differs_only_in_patch(old: &str, new: &str) -> bool {
    let without_patch = |text: &str| {
        let mut doc: DocumentMut = text.parse().ok()?;
        doc.remove("patch");
        Some(doc.to_string().trim().to_owned())
    };
    matches!((without_patch(old), without_patch(new)), (Some(a), Some(b)) if a == b)
}

pub struct PreTranslateStage {
    /// Hash of `HEAD` when setup ran
    base_commit: Option<String>,

    input: PreTranslateInput,
}

impl PreTranslateStage {
    /// Constructs a new stage from the given input.
    ///
    /// Must not have side effects.
    pub fn new(input: PreTranslateInput) -> Self {
        Self {
            base_commit: None,
            input,
        }
    }
}

impl Stage for PreTranslateStage {
    type Input = PreTranslateInput;

    type Output = ();

    type SetupError = GitError;

    type GateFailure = PreTranslateGateFailure;

    type GateExecutionError = PreTranslateGateError;

    fn name() -> &'static str {
        "pre-translate"
    }

    fn setup(&mut self) -> Result<(), Self::SetupError> {
        self.base_commit = Some(git::head_rev(&self.input.workspace_root)?);
        Ok(())
    }

    fn base_prompt(&self) -> String {
        BASE_PROMPT_TEMPLATE
            .replace("{manifest}", &self.input.manifest.display().to_string())
            .replace("{stderr}", &self.input.build_stderr)
    }

    fn briefing(&self) -> String {
        BRIEFING_TEMPLATE
            .replace(
                "{workspace_root}",
                &self.input.workspace_root.display().to_string(),
            )
            .replace("{manifest}", &self.input.manifest.display().to_string())
    }

    fn skills(&self) -> Vec<(&'static str, String)> {
        vec![("solana-host-build", SOLANA_HOST_BUILD_SKILL.to_owned())]
    }

    fn feedback_prompt(gate_error: &Self::GateFailure) -> String {
        match gate_error {
            PreTranslateGateFailure::ChangedFiles { files, base_commit } => format!(
                "These files changed, but you must not change files that git tracks, except \
                 `Cargo.lock` and the `[patch]` table of the workspace `Cargo.toml`:\n\n{}\n\n\
                 Restore each one with `git restore --source={base_commit} -- ':/<file>'`.",
                files
                    .iter()
                    .map(|f| format!("- `{}`", f.display()))
                    .collect::<Vec<_>>()
                    .join("\n")
            ),
            PreTranslateGateFailure::CargoBuildFailed { stderr } => format!(
                "`cargo build` failed with this output:\n\n```\n{stderr}\n```\n\n\
                 Fix the first error and run `cargo build` again until it passes.",
            ),
        }
    }

    fn gate_check(&self, _agent_summary: &str) -> Result<(), GateStop<Self>> {
        let base_commit = self.base_commit.as_ref().expect("Setup not run");

        // No tracked file may change, except `Cargo.lock` and the `patch` table
        // of the workspace `Cargo.toml`
        let toplevel =
            git::toplevel(&self.input.workspace_root).map_err(|e| GateStop::Error(e.into()))?;
        let workspace_manifest = self.input.workspace_root.join("Cargo.toml");
        let mut files = Vec::new();
        for f in
            git::changed_files(&toplevel, base_commit).map_err(|e| GateStop::Error(e.into()))?
        {
            if f.file_name() == Some("Cargo.lock".as_ref()) {
                continue;
            }
            if toplevel.join(&f) == workspace_manifest {
                let old = git::show_file(&toplevel, base_commit, &f)
                    .map_err(|e| GateStop::Error(e.into()))?;
                if let Ok(new) = fs::read_to_string(toplevel.join(&f))
                    && differs_only_in_patch(&old, &new)
                {
                    continue;
                }
            }
            files.push(f);
        }
        if !files.is_empty() {
            return Err(GateStop::Failure(PreTranslateGateFailure::ChangedFiles {
                files,
                base_commit: base_commit.clone(),
            }));
        }

        // The crate must build
        cargo::build(&self.input.manifest).map_err(|e| match e {
            CargoError::Build { stderr, .. } => {
                GateStop::Failure(PreTranslateGateFailure::CargoBuildFailed { stderr })
            }
            e => GateStop::Error(e.into()),
        })
    }
}
