use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::stage::{
    infer::InferStageSubmission,
    properties::{Properties, Property, PropertyId, PropertyKind},
};

#[derive(Debug, Clone, Serialize, Deserialize, thiserror::Error)]
pub enum InferGateFailure {
    #[error("The locked properties changed in the submission")]
    LockedChanged {
        /// The given locked properties that are not in the submission.
        missing: Vec<Property>,
        /// The given locked properties that the submission changed, each
        /// with its version in the submission.
        changed: Vec<(Property, Property)>,
        /// The locked properties of the submission that are not given
        /// locked properties.
        added: Vec<Property>,
    },
    /// Each pair of properties with the same id.
    #[error("{} pairs of properties have the same id", .0.len())]
    DuplicateIds(Vec<(Property, Property)>),
    /// Each pair of properties with the same title.
    #[error("{} pairs of properties have the same title", .0.len())]
    DuplicateTitles(Vec<(Property, Property)>),
    #[error("{} rules name no function", .0.len())]
    EmptyFns(Vec<Property>),
    #[error("{} given entrypoints are missing from the submission", .0.len())]
    MissingEntrypoints(Vec<String>),
    #[error("The submission has no entrypoints")]
    NoEntrypoints,
    #[error("{} entrypoints do not start with `crate::`", .0.len())]
    EntrypointPrefix(Vec<String>),
    /// Each entrypoint that is inside another, with that other entrypoint.
    #[error("{} entrypoints are inside another entrypoint", .0.len())]
    RedundantEntrypoints(Vec<(String, String)>),
}

#[derive(Debug, Clone, Serialize, Deserialize, thiserror::Error)]
pub enum InferGateExecutionError {}

pub(super) fn run_gate(
    submission: &InferStageSubmission,
    existings: &Properties,
    existing_eps: &[String],
) -> Result<Option<InferGateFailure>, InferGateExecutionError> {
    // No id is used twice
    if let Err(e) = duplicate_ids(&submission.properties) {
        return Ok(Some(e));
    }
    // Locked properties must not be touched.
    if let Err(e) = locked_changed(existings, &submission.properties) {
        return Ok(Some(e));
    }
    // No title is used twice
    if let Err(e) = duplicate_titles(&submission.properties) {
        return Ok(Some(e));
    }
    // Every rule names a function
    if let Err(e) = empty_fns(&submission.properties) {
        return Ok(Some(e));
    }
    // Every given entrypoint is returned
    if let Err(e) = missing_entrypoints(existing_eps, &submission.entrypoints) {
        return Ok(Some(e));
    }
    // There is an entrypoint
    if submission.entrypoints.is_empty() {
        return Ok(Some(InferGateFailure::NoEntrypoints));
    }
    // Every new entrypoint starts with `crate::`
    if let Err(e) = entrypoint_prefix(existing_eps, &submission.entrypoints) {
        return Ok(Some(e));
    }
    // No new entrypoint is inside another entrypoint
    if let Err(e) = redundant_entrypoints(existing_eps, &submission.entrypoints) {
        return Ok(Some(e));
    }

    Ok(None)
}

/// Fails if an entrypoint of `submitted` that is not in `existing` is another
/// entrypoint of `submitted` followed by `::` and more path segments.
fn redundant_entrypoints(
    existing: &[String],
    submitted: &[String],
) -> Result<(), InferGateFailure> {
    let mut redundant = Vec::new();
    for ep in submitted {
        if existing.contains(ep) {
            continue;
        }
        for other in submitted {
            if ep
                .strip_prefix(other.as_str())
                .is_some_and(|rest| rest.starts_with("::"))
            {
                redundant.push((ep.clone(), other.clone()));
            }
        }
    }

    if redundant.is_empty() {
        Ok(())
    } else {
        Err(InferGateFailure::RedundantEntrypoints(redundant))
    }
}

/// Fails if an entrypoint of `existing` is not in `submitted`.
fn missing_entrypoints(existing: &[String], submitted: &[String]) -> Result<(), InferGateFailure> {
    let mut missing = Vec::new();
    for ep in existing {
        if !submitted.contains(ep) {
            missing.push(ep.clone());
        }
    }

    if missing.is_empty() {
        Ok(())
    } else {
        Err(InferGateFailure::MissingEntrypoints(missing))
    }
}

/// Fails if an entrypoint of `submitted` that is not in `existing` does not
/// start with `crate::`.
fn entrypoint_prefix(existing: &[String], submitted: &[String]) -> Result<(), InferGateFailure> {
    let mut wrong = Vec::new();
    for ep in submitted {
        if !ep.starts_with("crate::") && !existing.contains(ep) {
            wrong.push(ep.clone());
        }
    }

    if wrong.is_empty() {
        Ok(())
    } else {
        Err(InferGateFailure::EntrypointPrefix(wrong))
    }
}

fn locked_changed(existings: &Properties, submitted: &Properties) -> Result<(), InferGateFailure> {
    let submitted_by_id: HashMap<PropertyId, &Property> =
        submitted.iter().map(|p| (p.id, p)).collect();
    let locked_ex: HashMap<PropertyId, &Property> =
        existings.locked().map(|p| (p.id, p)).collect();

    // Each given locked property must be in the submission, unchanged
    let mut missing = Vec::new();
    let mut changed = Vec::new();
    for (id, ex) in &locked_ex {
        match submitted_by_id.get(id) {
            None => missing.push((*ex).clone()),
            Some(sub) if sub != ex => changed.push(((*ex).clone(), (*sub).clone())),
            Some(_) => {}
        }
    }

    // No other property of the submission may be locked
    let mut added = Vec::new();
    for sub in submitted.locked() {
        if !locked_ex.contains_key(&sub.id) {
            added.push(sub.clone());
        }
    }

    if missing.is_empty() && changed.is_empty() && added.is_empty() {
        Ok(())
    } else {
        Err(InferGateFailure::LockedChanged {
            missing,
            changed,
            added,
        })
    }
}

fn duplicate_ids(props: &Properties) -> Result<(), InferGateFailure> {
    let mut by_id: HashMap<PropertyId, &Property> = HashMap::with_capacity(props.0.len());
    let mut duplicates = Vec::new();
    for p in props.iter() {
        if let Some(old) = by_id.insert(p.id, p) {
            duplicates.push((old.clone(), p.clone()));
        }
    }

    if duplicates.is_empty() {
        Ok(())
    } else {
        Err(InferGateFailure::DuplicateIds(duplicates))
    }
}

/// Fails if two properties have the same title, unless both are locked.
fn duplicate_titles(props: &Properties) -> Result<(), InferGateFailure> {
    let mut by_title: HashMap<&str, &Property> = HashMap::with_capacity(props.0.len());
    let mut duplicates = Vec::new();
    for p in props.iter() {
        if let Some(old) = by_title.insert(&p.title, p)
            && !(old.locked && p.locked)
        {
            duplicates.push((old.clone(), p.clone()));
        }
    }

    if duplicates.is_empty() {
        Ok(())
    } else {
        Err(InferGateFailure::DuplicateTitles(duplicates))
    }
}

/// Fails if a rule that is not locked has an empty `fns`.
fn empty_fns(props: &Properties) -> Result<(), InferGateFailure> {
    let mut empty = Vec::new();
    for p in props.iter() {
        if let PropertyKind::Rule { fns } = &p.kind
            && fns.is_empty()
            && !p.locked
        {
            empty.push(p.clone());
        }
    }

    if empty.is_empty() {
        Ok(())
    } else {
        Err(InferGateFailure::EmptyFns(empty))
    }
}
