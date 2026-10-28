use std::{fs, path::PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use toml_edit::DocumentMut;

use crate::{
    stage::{GateStop, Never, Stage},
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

/// The judge prompt of the pre-translate stage, with `{workspace_root}`,
/// `{base_commit}`, `{briefing}` and `{report}` standing for the workspace
/// root, the hash of `HEAD` when setup ran, the agent briefing and the agent's
/// report.
const JUDGE_PROMPT_TEMPLATE: &str = include_str!("res/judge.md");

pub struct PreTranslateInput {
    /// Absolute path to the workspace of the Rust project.
    pub workspace_root: PathBuf,
    /// Absolute path to the `Cargo.toml` of the crate.
    pub manifest: PathBuf,
    /// The stderr of the failed `cargo build` of the crate.
    pub build_stderr: String,
}

/// Every change that the agent made since the start of the stage.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct PreTranslateReport {
    /// Each change, including each patched copy of a dependency.
    pub changes: Vec<PreTranslateChange>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct PreTranslateChange {
    /// The file, relative to the workspace root.
    pub file: String,
    /// What changed.
    pub change: String,
    /// Why the crate needs the change to build on the host.
    pub reason: String,
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

/// An agent makes the crate build on the host without changing what it
/// computes on its target. The gate checks that `cargo build` passes and that
/// no tracked file changed except `.rs` files, `Cargo.lock` and the `patch`
/// table of the workspace `Cargo.toml`. The judge checks each change against
/// the rules of the briefing.
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

    type Report = PreTranslateReport;

    type Blocker = Never;

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
                 `.rs` files, `Cargo.lock` and the `[patch]` table of the workspace \
                 `Cargo.toml`:\n\n{}\n\n\
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

    fn gate_check(&self, _report: &PreTranslateReport) -> Result<(), GateStop<Self>> {
        let base_commit = self.base_commit.as_ref().expect("Setup not run");

        // No tracked file may change, except `.rs` files, `Cargo.lock` and the
        // `patch` table of the workspace `Cargo.toml`
        let toplevel =
            git::toplevel(&self.input.workspace_root).map_err(|e| GateStop::Error(e.into()))?;
        let workspace_manifest = self.input.workspace_root.join("Cargo.toml");
        let mut files = Vec::new();
        for f in
            git::changed_files(&toplevel, base_commit).map_err(|e| GateStop::Error(e.into()))?
        {
            if f.file_name() == Some("Cargo.lock".as_ref()) || f.extension() == Some("rs".as_ref())
            {
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

    fn judge_prompt(&self, _output: &Self::Output, report: &PreTranslateReport) -> Option<String> {
        let base_commit = self.base_commit.as_ref().expect("Setup not run");
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace(
                    "{workspace_root}",
                    &self.input.workspace_root.display().to_string(),
                )
                .replace("{base_commit}", base_commit)
                .replace("{briefing}", &self.briefing())
                .replace(
                    "{report}",
                    &serde_json::to_string_pretty(report).expect("the report serializes"),
                ),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn judge_prompt_contains_the_report() {
        let mut stage = PreTranslateStage::new(PreTranslateInput {
            workspace_root: PathBuf::from("/ws"),
            manifest: PathBuf::from("/ws/Cargo.toml"),
            build_stderr: String::new(),
        });
        stage.base_commit = Some("abc123".to_owned());
        let report = PreTranslateReport {
            changes: vec![PreTranslateChange {
                file: "src/lib.rs".to_owned(),
                change: "gated a size check".to_owned(),
                reason: "it fails on the host".to_owned(),
            }],
        };

        let prompt = stage.judge_prompt(&(), &report).unwrap();

        assert!(prompt.contains(&serde_json::to_string_pretty(&report).unwrap()));
        assert!(!prompt.contains("{report}"));
    }
}
