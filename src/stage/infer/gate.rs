use std::collections::HashMap;

use itertools::{EitherOrBoth, Itertools};
use serde::{Deserialize, Serialize};

use crate::stage::{
    infer::InferStageSubmission,
    properties::{Properties, Property, PropertyId},
};

#[derive(Debug, Clone, Serialize, Deserialize, thiserror::Error)]
pub enum InferGateFailure {
    #[error("The locked properties changed in the submission")]
    LockedChanged {
        existing: Properties,
        submitted: Properties,
    },
    #[error("Two properties have the same id {0}")]
    DuplicateId(usize, Property, Property),
}

#[derive(Debug, Clone, Serialize, Deserialize, thiserror::Error)]
pub enum InferGateExecutionError {}

pub(super) fn run_gate(
    submission: &InferStageSubmission,
    existings: &Properties,
) -> Result<Option<InferGateFailure>, InferGateExecutionError> {
    // No id is used twice
    if let Err(e) = duplicate_ids(&submission.properties) {
        return Ok(Some(e));
    }
    // Locked properties must not be touched.
    if let Err(e) = locked_changed(existings, &submission.properties) {
        return Ok(Some(e));
    }

    Ok(None)
}

fn locked_changed(existings: &Properties, submitted: &Properties) -> Result<(), InferGateFailure> {
    let locked_ex: Properties = existings
        .locked()
        .cloned()
        .sorted_unstable_by_key(|p| p.id)
        .collect();
    let locked_sub: Properties = submitted
        .locked()
        .cloned()
        .sorted_unstable_by_key(|p| p.id)
        .collect();

    if locked_ex != locked_sub {
        Err(InferGateFailure::LockedChanged {
            existing: locked_ex,
            submitted: locked_sub,
        })
    } else {
        Ok(())
    }
}

fn duplicate_ids(props: &Properties) -> Result<(), InferGateFailure> {
    let mut by_id: HashMap<PropertyId, &Property> = HashMap::with_capacity(props.0.len());
    for p in props.iter() {
        if let Some(old) = by_id.insert(p.id, p) {
            return Err(InferGateFailure::DuplicateId(p.id, old.clone(), p.clone()));
        }
    }

    Ok(())
}
