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
    if let Some(e) = duplicate_ids(&submission.properties) {
        return Ok(Some(e));
    }
    // Locked properties must not be touched.
    if let Some(e) = locked_changed(existings, &submission.properties) {
        return Ok(Some(e));
    }
    // No title is used twice
    if let Some(e) = duplicate_titles(&submission.properties) {
        return Ok(Some(e));
    }
    // Every rule names a function
    if let Some(e) = empty_fns(&submission.properties) {
        return Ok(Some(e));
    }
    // Every given entrypoint is returned
    if let Some(e) = missing_entrypoints(existing_eps, &submission.entrypoints) {
        return Ok(Some(e));
    }
    // There is an entrypoint
    if submission.entrypoints.is_empty() {
        return Ok(Some(InferGateFailure::NoEntrypoints));
    }
    // Every new entrypoint starts with `crate::`
    if let Some(e) = entrypoint_prefix(existing_eps, &submission.entrypoints) {
        return Ok(Some(e));
    }
    // No new entrypoint is inside another entrypoint
    if let Some(e) = redundant_entrypoints(existing_eps, &submission.entrypoints) {
        return Ok(Some(e));
    }

    Ok(None)
}

/// Fails if an entrypoint of `submitted` that is not in `existing` is another
/// entrypoint of `submitted` followed by `::` and more path segments.
fn redundant_entrypoints(existing: &[String], submitted: &[String]) -> Option<InferGateFailure> {
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

    (!redundant.is_empty()).then(|| InferGateFailure::RedundantEntrypoints(redundant))
}

/// Fails if an entrypoint of `existing` is not in `submitted`.
fn missing_entrypoints(existing: &[String], submitted: &[String]) -> Option<InferGateFailure> {
    let mut missing = Vec::new();
    for ep in existing {
        if !submitted.contains(ep) {
            missing.push(ep.clone());
        }
    }

    (!missing.is_empty()).then(|| InferGateFailure::MissingEntrypoints(missing))
}

/// Fails if an entrypoint of `submitted` that is not in `existing` does not
/// start with `crate::`.
fn entrypoint_prefix(existing: &[String], submitted: &[String]) -> Option<InferGateFailure> {
    let mut wrong = Vec::new();
    for ep in submitted {
        if !ep.starts_with("crate::") && !existing.contains(ep) {
            wrong.push(ep.clone());
        }
    }

    (!wrong.is_empty()).then(|| InferGateFailure::EntrypointPrefix(wrong))
}

fn locked_changed(existings: &Properties, submitted: &Properties) -> Option<InferGateFailure> {
    let submitted_by_id: HashMap<PropertyId, &Property> =
        submitted.iter().map(|p| (p.id, p)).collect();
    let locked_ex: HashMap<PropertyId, &Property> = existings.locked().map(|p| (p.id, p)).collect();

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
        None
    } else {
        Some(InferGateFailure::LockedChanged {
            missing,
            changed,
            added,
        })
    }
}

fn duplicate_ids(props: &Properties) -> Option<InferGateFailure> {
    let mut by_id: HashMap<PropertyId, &Property> = HashMap::with_capacity(props.0.len());
    let mut duplicates = Vec::new();
    for p in props.iter() {
        if let Some(old) = by_id.insert(p.id, p) {
            duplicates.push((old.clone(), p.clone()));
        }
    }

    (!duplicates.is_empty()).then(|| InferGateFailure::DuplicateIds(duplicates))
}

/// Fails if two properties have the same title, unless both are locked.
fn duplicate_titles(props: &Properties) -> Option<InferGateFailure> {
    let mut by_title: HashMap<&str, &Property> = HashMap::with_capacity(props.0.len());
    let mut duplicates = Vec::new();
    for p in props.iter() {
        if let Some(old) = by_title.insert(&p.title, p)
            && !(old.locked && p.locked)
        {
            duplicates.push((old.clone(), p.clone()));
        }
    }

    (!duplicates.is_empty()).then(|| InferGateFailure::DuplicateTitles(duplicates))
}

/// Fails if a rule that is not locked has an empty `fns`.
fn empty_fns(props: &Properties) -> Option<InferGateFailure> {
    let mut empty = Vec::new();
    for p in props.iter() {
        if let PropertyKind::Rule { fns } = &p.kind
            && fns.is_empty()
            && !p.locked
        {
            empty.push(p.clone());
        }
    }

    (!empty.is_empty()).then(|| InferGateFailure::EmptyFns(empty))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A rule about `a::f` with the given id, title and `locked`.
    fn rule(id: PropertyId, title: &str, locked: bool) -> Property {
        Property {
            id,
            kind: PropertyKind::Rule {
                fns: vec!["a::f".to_owned()],
            },
            title: title.to_owned(),
            natspec: String::new(),
            locked,
        }
    }

    fn eps(entrypoints: &[&str]) -> Vec<String> {
        entrypoints.iter().map(|e| (*e).to_owned()).collect()
    }

    #[test]
    fn duplicate_ids_reports_each_pair() {
        let props = Properties(vec![
            rule(1, "a", false),
            rule(1, "b", false),
            rule(2, "c", false),
        ]);
        let Some(InferGateFailure::DuplicateIds(pairs)) = duplicate_ids(&props) else {
            panic!("expected DuplicateIds");
        };
        assert_eq!(pairs, [(rule(1, "a", false), rule(1, "b", false))]);
    }

    #[test]
    fn locked_changed_reports_a_changed_locked_property_as_changed() {
        let given = Properties(vec![rule(1, "a", true)]);
        let mut revised = rule(1, "a", true);
        revised.natspec = "new".to_owned();
        let submitted = Properties(vec![revised.clone()]);
        let Some(InferGateFailure::LockedChanged {
            missing,
            changed,
            added,
        }) = locked_changed(&given, &submitted)
        else {
            panic!("expected LockedChanged");
        };
        assert!(missing.is_empty());
        assert_eq!(changed, [(rule(1, "a", true), revised)]);
        assert!(added.is_empty());
    }

    #[test]
    fn locked_changed_reports_an_unlocked_locked_property_as_changed() {
        let given = Properties(vec![rule(1, "a", true)]);
        let submitted = Properties(vec![rule(1, "a", false)]);
        let Some(InferGateFailure::LockedChanged {
            missing,
            changed,
            added,
        }) = locked_changed(&given, &submitted)
        else {
            panic!("expected LockedChanged");
        };
        assert!(missing.is_empty());
        assert_eq!(changed, [(rule(1, "a", true), rule(1, "a", false))]);
        assert!(added.is_empty());
    }

    #[test]
    fn locked_changed_reports_missing_and_added_locked_properties() {
        let given = Properties(vec![rule(1, "a", true)]);
        let submitted = Properties(vec![rule(2, "b", true)]);
        let Some(InferGateFailure::LockedChanged {
            missing,
            changed,
            added,
        }) = locked_changed(&given, &submitted)
        else {
            panic!("expected LockedChanged");
        };
        assert_eq!(missing, [rule(1, "a", true)]);
        assert!(changed.is_empty());
        assert_eq!(added, [rule(2, "b", true)]);
    }

    #[test]
    fn locked_changed_allows_changes_to_unlocked_properties() {
        let given = Properties(vec![
            rule(1, "a", true),
            rule(2, "b", false),
            rule(3, "c", false),
        ]);
        // 2 is revised, 3 is removed, 4 is new
        let submitted = Properties(vec![
            rule(1, "a", true),
            rule(2, "b2", false),
            rule(4, "d", false),
        ]);
        assert!(locked_changed(&given, &submitted).is_none());
    }

    #[test]
    fn duplicate_titles_reports_an_unlocked_property_with_the_title_of_a_locked_one() {
        let props = Properties(vec![rule(1, "a", true), rule(2, "a", false)]);
        let Some(InferGateFailure::DuplicateTitles(pairs)) = duplicate_titles(&props) else {
            panic!("expected DuplicateTitles");
        };
        assert_eq!(pairs, [(rule(1, "a", true), rule(2, "a", false))]);
    }

    #[test]
    fn duplicate_titles_allows_two_locked_properties_with_the_same_title() {
        let props = Properties(vec![rule(1, "a", true), rule(2, "a", true)]);
        assert!(duplicate_titles(&props).is_none());
    }

    #[test]
    fn empty_fns_reports_only_unlocked_rules() {
        let empty_rule = |id, locked| Property {
            kind: PropertyKind::Rule { fns: Vec::new() },
            ..rule(id, "a", locked)
        };
        let invariant = Property {
            kind: PropertyKind::Invariant {
                r#struct: "a::S".to_owned(),
            },
            ..rule(3, "c", false)
        };
        let props = Properties(vec![empty_rule(1, true), empty_rule(2, false), invariant]);
        let Some(InferGateFailure::EmptyFns(rules)) = empty_fns(&props) else {
            panic!("expected EmptyFns");
        };
        assert_eq!(rules, [empty_rule(2, false)]);
    }

    #[test]
    fn missing_entrypoints_reports_the_given_entrypoints_that_are_not_returned() {
        let given = eps(&["crate::a", "crate::b"]);
        let submitted = eps(&["crate::b", "crate::c"]);
        let Some(InferGateFailure::MissingEntrypoints(missing)) =
            missing_entrypoints(&given, &submitted)
        else {
            panic!("expected MissingEntrypoints");
        };
        assert_eq!(missing, ["crate::a"]);
    }

    #[test]
    fn entrypoint_prefix_reports_only_new_entrypoints() {
        let given = eps(&["a::f"]);
        let submitted = eps(&["a::f", "b::g", "crate::c"]);
        let Some(InferGateFailure::EntrypointPrefix(wrong)) = entrypoint_prefix(&given, &submitted)
        else {
            panic!("expected EntrypointPrefix");
        };
        assert_eq!(wrong, ["b::g"]);
    }

    #[test]
    fn redundant_entrypoints_reports_a_new_entrypoint_inside_another() {
        let submitted = eps(&["crate::a", "crate::a::f"]);
        let Some(InferGateFailure::RedundantEntrypoints(redundant)) =
            redundant_entrypoints(&[], &submitted)
        else {
            panic!("expected RedundantEntrypoints");
        };
        assert_eq!(
            redundant,
            [("crate::a::f".to_owned(), "crate::a".to_owned())]
        );
    }

    #[test]
    fn redundant_entrypoints_compares_whole_path_segments() {
        let submitted = eps(&["crate::a", "crate::ab::f"]);
        assert!(redundant_entrypoints(&[], &submitted).is_none());
    }

    #[test]
    fn redundant_entrypoints_allows_a_given_entrypoint_inside_a_new_one() {
        let given = eps(&["crate::a::f"]);
        let submitted = eps(&["crate::a::f", "crate::a"]);
        assert!(redundant_entrypoints(&given, &submitted).is_none());
    }

    #[test]
    fn run_gate_fails_without_entrypoints() {
        let submission = InferStageSubmission {
            properties: Properties(vec![rule(1, "a", false)]),
            entrypoints: Vec::new(),
        };
        let failure = run_gate(&submission, &Properties::default(), &[]).unwrap();
        assert!(matches!(failure, Some(InferGateFailure::NoEntrypoints)));
    }

    #[test]
    fn run_gate_passes_a_submission_that_adds_to_the_given_one() {
        let given = Properties(vec![rule(1, "a", true)]);
        let submission = InferStageSubmission {
            properties: Properties(vec![rule(1, "a", true), rule(2, "b", false)]),
            entrypoints: eps(&["crate::a", "crate::b"]),
        };
        let failure = run_gate(&submission, &given, &eps(&["crate::a"])).unwrap();
        assert!(failure.is_none());
    }
}
