mod gate;

use std::path::PathBuf;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    config::Platform,
    stage::{Stage, StageName, infer::gate::run_gate, properties::Properties},
};

const BRIEFING: &str = include_str!("res/briefing.md");

const BASE_PROMPT_TEMPLATE: &str = include_str!("res/prompt.md");

const DEFI_PROPERTY_CLASSES_SKILL: &str = include_str!("res/defi-property-classes.md");

const SOLANA_PROPERTY_CLASSES_SKILL: &str = include_str!("res/solana-property-classes.md");

#[derive(Serialize, Deserialize)]
pub struct InferStageInput {
    existing_properties: Properties,
    existing_entrypoints: Vec<String>,
    design_doc: Option<PathBuf>,
    platform: Platform,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema, Clone)]
pub struct InferStageSubmission {
    properties: Properties,
    entrypoints: Vec<String>,
}

#[derive(Serialize, Deserialize)]
pub struct InferStage {
    existing_props: Properties,
    existing_eps: Vec<String>,
    design_doc: Option<PathBuf>,
    platform: Platform,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema, thiserror::Error)]
pub enum InferStageBlocker {}

#[derive(Debug, Clone, Serialize, Deserialize, derive_more::Display, thiserror::Error)]
pub struct InferSetupError;

impl Stage for InferStage {
    const NAME: super::StageName = StageName::Infer;

    type Input = InferStageInput;

    type Submission = InferStageSubmission;

    type Blocker = InferStageBlocker;

    type GateFailure = gate::InferGateFailure;

    type GateExecutionError = gate::InferGateExecutionError;

    type SetupError = InferSetupError;

    fn setup(input: Self::Input) -> Result<Self, Self::SetupError> {
        // We do not need a setup
        Ok(Self {
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
        BRIEFING.replace(
            "{skills}",
            match &self.platform {
                Platform::Generic => "",
                Platform::Solana { .. } => {
                    " Before you draft, work through the skills `lusterna:defi-property-classes` and \
                     `lusterna:solana-property-classes`."
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
        todo!()
    }

    fn gate_check(
        &self,
        submission: &Self::Submission,
    ) -> Result<Option<Self::GateFailure>, Self::GateExecutionError> {
        run_gate(submission, &self.existing_props, &self.existing_eps)
    }

    fn judge_prompt(&self, submission: &Self::Submission) -> Option<String> {
        todo!()
    }
}
