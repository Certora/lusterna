use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::stage::{
    infer::InferStageSubmission,
    properties::{Properties, Property, PropertyId, PropertyKind},
};

#[derive(Debug, Clone, Serialize, Deserialize, derive_more::Display)]
pub enum InferGateFailure {
    #[display("the locked properties changed in the submission")]
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
    #[display("{} pairs of properties have the same id", _0.len())]
    DuplicateIds(Vec<(Property, Property)>),
    /// Each pair of properties with the same title.
    #[display("{} pairs of properties have the same title", _0.len())]
    DuplicateTitles(Vec<(Property, Property)>),
    #[display("{} rules name no function", _0.len())]
    EmptyFns(Vec<Property>),
    #[display("{} given entrypoints are missing from the submission", _0.len())]
    MissingEntrypoints(Vec<String>),
    #[display("the submission has no entrypoints")]
    NoEntrypoints,
    #[display("{} entrypoints do not start with `crate::`", _0.len())]
    EntrypointPrefix(Vec<String>),
    /// Each entrypoint that is inside another, with that other entrypoint.
    #[display("{} entrypoints are inside another entrypoint", _0.len())]
    RedundantEntrypoints(Vec<(String, String)>),
}

impl InferGateFailure {
    pub(super) fn to_feedback_prompt(&self) -> String {
        let list = |items: Vec<String>| {
            items
                .iter()
                .map(|i| format!("- {i}"))
                .collect::<Vec<_>>()
                .join("\n")
        };
        let name = |p: &Property| format!("{} (\"{}\")", p.id, p.title);
        let json = |ps: Vec<&Property>| {
            serde_json::to_string_pretty(&ps).expect("the properties serialize")
        };
        match self {
            InferGateFailure::LockedChanged {
                missing,
                changed,
                added,
            } => {
                let mut prompt = "A property whose `locked` is `true` must be in your \
                                  submission exactly as it was given, and no other property may \
                                  have `locked` set to `true`."
                    .to_string();
                if !missing.is_empty() {
                    prompt += &format!(
                        "\n\nThese given properties are missing. Add them \
                         unchanged:\n\n```json\n{}\n```",
                        json(missing.iter().collect())
                    );
                }
                if !changed.is_empty() {
                    prompt += &format!(
                        "\n\nYou changed these given properties. Return them as they are \
                         here:\n\n```json\n{}\n```",
                        json(changed.iter().map(|(given, _)| given).collect())
                    );
                }
                if !added.is_empty() {
                    prompt += &format!(
                        "\n\nThese properties are not given locked properties. Set their \
                         `locked` to `false`:\n\n{}",
                        list(added.iter().map(name).collect())
                    );
                }
                prompt
            }
            InferGateFailure::DuplicateIds(pairs) => format!(
                "These pairs of properties have the same `id`:\n\n{}\n\n\
                 Give each new property an `id` that no other property has. Do not change the \
                 `id` of a given property.",
                list(
                    pairs
                        .iter()
                        .map(|(a, b)| format!("{} and {}", name(a), name(b)))
                        .collect()
                )
            ),
            InferGateFailure::DuplicateTitles(pairs) => format!(
                "These pairs of properties have the same `title`:\n\n{}\n\n\
                 Change the `title` of the property whose `locked` is `false`. If the two \
                 properties state the same, remove that property instead.",
                list(
                    pairs
                        .iter()
                        .map(|(a, b)| format!("{} and {}", name(a), name(b)))
                        .collect()
                )
            ),
            InferGateFailure::EmptyFns(rules) => format!(
                "These rules have an empty `fns`:\n\n{}\n\n\
                 Name the functions that each rule is about.",
                list(rules.iter().map(name).collect())
            ),
            InferGateFailure::MissingEntrypoints(entrypoints) => format!(
                "These given entrypoints are missing from your submission:\n\n{}\n\n\
                 Add them unchanged.",
                list(entrypoints.iter().map(|e| format!("`{e}`")).collect())
            ),
            InferGateFailure::NoEntrypoints => "Your submission has no entrypoints. Add the \
                                                entrypoints that the properties need."
                .to_string(),
            InferGateFailure::EntrypointPrefix(entrypoints) => format!(
                "These entrypoints do not start with `crate::`:\n\n{}\n\n\
                 Write each as a path from the crate root that starts with `crate::`.",
                list(entrypoints.iter().map(|e| format!("`{e}`")).collect())
            ),
            InferGateFailure::RedundantEntrypoints(pairs) => format!(
                "These entrypoints are inside another entrypoint:\n\n{}\n\n\
                 Remove each of them, or replace the outer entrypoint with the entrypoints \
                 inside it that the properties need.",
                list(
                    pairs
                        .iter()
                        .map(|(inner, outer)| format!("`{inner}` is inside `{outer}`"))
                        .collect()
                )
            ),
        }
    }
}

pub(super) fn run_gate(
    submission: &InferStageSubmission,
    existing: &Properties,
    existing_eps: &[String],
) -> Option<InferGateFailure> {
    // No id is used twice
    if let Some(e) = duplicate_ids(&submission.properties) {
        return Some(e);
    }
    // Locked properties must not be touched.
    if let Some(e) = locked_changed(existing, &submission.properties) {
        return Some(e);
    }
    // No title is used twice
    if let Some(e) = duplicate_titles(&submission.properties) {
        return Some(e);
    }
    // Every rule names a function
    if let Some(e) = empty_fns(&submission.properties) {
        return Some(e);
    }
    // Every given entrypoint is returned
    if let Some(e) = missing_entrypoints(existing_eps, &submission.entrypoints) {
        return Some(e);
    }
    // There is an entrypoint
    if submission.entrypoints.is_empty() {
        return Some(InferGateFailure::NoEntrypoints);
    }
    // Every new entrypoint starts with `crate::`
    if let Some(e) = entrypoint_prefix(existing_eps, &submission.entrypoints) {
        return Some(e);
    }
    // No new entrypoint is inside another entrypoint
    redundant_entrypoints(existing_eps, &submission.entrypoints)
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

fn locked_changed(existing: &Properties, submitted: &Properties) -> Option<InferGateFailure> {
    let submitted_by_id: HashMap<PropertyId, &Property> =
        submitted.iter().map(|p| (p.id, p)).collect();
    let locked_ex: HashMap<PropertyId, &Property> = existing.locked().map(|p| (p.id, p)).collect();

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
            design_doc: None,
        };
        let failure = run_gate(&submission, &Properties::default(), &[]);
        assert!(matches!(failure, Some(InferGateFailure::NoEntrypoints)));
    }

    #[test]
    fn run_gate_passes_a_submission_that_adds_to_the_given_one() {
        let given = Properties(vec![rule(1, "a", true)]);
        let submission = InferStageSubmission {
            properties: Properties(vec![rule(1, "a", true), rule(2, "b", false)]),
            entrypoints: eps(&["crate::a", "crate::b"]),
            design_doc: None,
        };
        let failure = run_gate(&submission, &given, &eps(&["crate::a"]));
        assert!(failure.is_none());
    }
}
