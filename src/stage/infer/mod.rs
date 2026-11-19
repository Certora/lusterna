mod gate;

use std::{convert::Infallible, path::PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    config::Platform,
    stage::{Never, Stage, StageName, infer::gate::run_gate, properties::Properties},
};

const BRIEFING: &str = include_str!("res/briefing.md");

const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

const JUDGE_PROMPT_TEMPLATE: &str = include_str!("res/judge.md");

const DEFI_PROPERTY_CLASSES_SKILL: &str = include_str!("res/defi-property-classes.md");

const SOLANA_PROPERTY_CLASSES_SKILL: &str = include_str!("res/solana-property-classes.md");

#[derive(Serialize, Deserialize)]
pub struct InferStageInput {
    /// Absolute path to the root of the crate.
    pub crate_root: PathBuf,
    pub existing_properties: Properties,
    pub existing_entrypoints: Vec<String>,
    pub design_doc: Option<PathBuf>,
    pub platform: Platform,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema, Clone)]
pub struct InferStageSubmission {
    pub properties: Properties,
    pub entrypoints: Vec<String>,
    /// The design document that you used, relative to the workspace root.
    /// `null` if you used none.
    pub design_doc: Option<PathBuf>,
}

#[derive(Serialize, Deserialize)]
pub struct InferStage {
    crate_root: PathBuf,
    existing_props: Properties,
    existing_eps: Vec<String>,
    design_doc: Option<PathBuf>,
    platform: Platform,
}

impl Stage for InferStage {
    const NAME: super::StageName = StageName::Infer;

    type Input = InferStageInput;

    type Submission = InferStageSubmission;

    type Blocker = Never;

    type GateFailure = gate::InferGateFailure;

    type GateExecutionError = Infallible;

    type SetupError = Infallible;

    fn setup(input: Self::Input) -> Result<Self, Self::SetupError> {
        // We do not need a setup
        Ok(Self {
            crate_root: input.crate_root,
            existing_props: input.existing_properties,
            existing_eps: input.existing_entrypoints,
            design_doc: input.design_doc,
            platform: input.platform,
        })
    }

    fn base_prompt(&self) -> String {
        let design_doc = match &self.design_doc {
            Some(path) => format!("`{}`", path.display()),
            None => "none".to_owned(),
        };
        let entrypoints = if self.existing_eps.is_empty() {
            "none".to_owned()
        } else {
            self.existing_eps
                .iter()
                .map(|e| format!("`{e}`"))
                .collect::<Vec<_>>()
                .join(", ")
        };
        BASE_PROMPT_TEMPLATE
            .replace("{design_doc}", &design_doc)
            .replace("{entrypoints}", &entrypoints)
            .replace(
                "{properties}",
                &serde_json::to_string_pretty(&self.existing_props)
                    .expect("the properties serialize"),
            )
    }

    fn briefing(&self) -> String {
        BRIEFING
            .replace("{crate_root}", &self.crate_root.display().to_string())
            .replace(
                "{skills}",
                match &self.platform {
                    Platform::Generic => "",
                    Platform::Solana { .. } => {
                        " Before you draft, work through the skills \
                         `lusterna:defi-property-classes` and `lusterna:solana-property-classes`."
                    }
                },
            )
    }

    fn skills(&self) -> Vec<(&'static str, String)> {
        match &self.platform {
            Platform::Generic => Vec::new(),
            Platform::Solana { .. } => vec![
                (
                    "defi-property-classes",
                    DEFI_PROPERTY_CLASSES_SKILL.to_owned(),
                ),
                (
                    "solana-property-classes",
                    SOLANA_PROPERTY_CLASSES_SKILL.to_owned(),
                ),
            ],
        }
    }

    fn feedback_prompt(gate_failed: &Self::GateFailure) -> String {
        gate_failed.to_feedback_prompt()
    }

    fn gate_check(
        &self,
        submission: &Self::Submission,
    ) -> Result<Option<Self::GateFailure>, Self::GateExecutionError> {
        Ok(run_gate(
            submission,
            &self.existing_props,
            &self.existing_eps,
        ))
    }

    fn judge_prompt(&self, submission: &Self::Submission) -> Option<String> {
        Some(
            JUDGE_PROMPT_TEMPLATE
                .replace("{crate_root}", &self.crate_root.display().to_string())
                .replace("{briefing}", &self.briefing())
                .replace(
                    "{submission}",
                    &serde_json::to_string_pretty(submission).expect("the submission serializes"),
                ),
        )
    }
}
