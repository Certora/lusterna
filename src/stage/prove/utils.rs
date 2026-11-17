use std::collections::BTreeMap;

use crate::{
    stage::{
        properties::PropertyId,
        prove::{PropertyProofStatus, ProveSubmission, TheoremStatus},
    },
    utils::spec_check::TheoremCheck,
};

/// Returns the theorems with the label of the property `id`, each with its
/// status in `submission`. Leaves out a theorem without a status.
pub(super) fn labelled_theorems<'a>(
    id: PropertyId,
    submission: &ProveSubmission,
    spec_theorems: &'a [TheoremCheck],
) -> Vec<(&'a TheoremCheck, TheoremStatus)> {
    let mut theorems = Vec::new();
    for theorem in spec_theorems {
        if theorem.id != Some(id) {
            continue;
        }
        if let Some(status) = submission.theorems.get(&theorem.name) {
            theorems.push((theorem, status.clone()));
        }
    }
    theorems
}

/// Returns the status of a rule property from the statuses of its theorems.
pub(super) fn rule_status(theorems: Vec<(&TheoremCheck, TheoremStatus)>) -> PropertyProofStatus {
    let mut statuses = BTreeMap::new();
    for (theorem, status) in theorems {
        statuses.insert(theorem.name.clone(), status);
    }
    PropertyProofStatus::Rule { theorems: statuses }
}

/// Returns the status of an invariant property from the statuses of its
/// theorems. A theorem whose triple calls one of `base_case_functions` is a
/// base case, and every other theorem is an induction step. Each theorem is
/// listed under the function that its triple calls, or under its own name if
/// `Lusterna.Spec.check` reports no function for it.
pub(super) fn induction_status(
    theorems: Vec<(&TheoremCheck, TheoremStatus)>,
    base_case_functions: &[String],
) -> PropertyProofStatus {
    let mut base_cases: BTreeMap<String, Vec<(String, TheoremStatus)>> = BTreeMap::new();
    let mut steps: BTreeMap<String, Vec<(String, TheoremStatus)>> = BTreeMap::new();
    for (theorem, status) in theorems {
        let function = match &theorem.function {
            Some(function) => function.clone(),
            None => theorem.name.clone(),
        };
        let group = if base_case_functions.contains(&function) {
            &mut base_cases
        } else {
            &mut steps
        };
        group
            .entry(function)
            .or_default()
            .push((theorem.name.clone(), status));
    }
    PropertyProofStatus::Induction { base_cases, steps }
}

#[cfg(test)]
pub(super) mod tests {
    use std::path::PathBuf;

    use super::*;
    use crate::stage::{
        formalize::{Induction, PropertyFormalizationStatus},
        properties::{Properties, Property, PropertyKind},
        prove::{AdditionalAssumption, AssumptionPurpose, ProveInput},
    };

    /// A rule property with the id 1 and an invariant property with the id 2.
    pub(crate) fn properties() -> Properties {
        Properties(vec![
            Property {
                id: 1,
                kind: PropertyKind::Rule {
                    fns: vec!["module::f".to_owned()],
                },
                title: "f adds".to_owned(),
                natspec: String::new(),
            },
            Property {
                id: 2,
                kind: PropertyKind::Invariant {
                    r#struct: "module::Struct".to_owned(),
                },
                title: "Inv holds".to_owned(),
                natspec: String::new(),
            },
        ])
    }

    /// An input in which both properties are formalized, and `Struct::new`
    /// creates the struct of the invariant.
    pub(crate) fn input() -> ProveInput {
        ProveInput {
            extracted: PathBuf::new(),
            lean_root: PathBuf::new(),
            properties: PathBuf::new(),
            property_status: BTreeMap::from([
                (
                    1,
                    PropertyFormalizationStatus::Formalized {
                        file: PathBuf::from("Spec/FAdds.lean"),
                        theorems: vec!["Spec.FAdds.f_adds".to_owned()],
                        exceptions: BTreeMap::new(),
                    },
                ),
                (
                    2,
                    PropertyFormalizationStatus::Formalized {
                        file: PathBuf::from("Spec/Inv.lean"),
                        theorems: vec!["Spec.Inv.new".to_owned(), "Spec.Inv.f".to_owned()],
                        exceptions: BTreeMap::new(),
                    },
                ),
            ]),
            induction: BTreeMap::from([(
                "module::Struct".to_owned(),
                Induction {
                    base_cases: vec!["module.Struct.new".to_owned()],
                    steps: vec!["module.Struct.f".to_owned()],
                    unmodelled_steps: BTreeMap::new(),
                    helpers: BTreeMap::new(),
                },
            )]),
        }
    }

    /// A theorem with the label `id` whose triple calls `function`.
    fn labelled(name: &str, id: PropertyId, function: &str, proven: bool) -> TheoremCheck {
        TheoremCheck {
            id: Some(id),
            function: Some(function.to_owned()),
            ..unlabelled(name, proven)
        }
    }

    /// A theorem without a label. If it is not `proven`, it depends on
    /// `sorryAx`.
    pub(crate) fn unlabelled(name: &str, proven: bool) -> TheoremCheck {
        let axioms = if proven {
            Vec::new()
        } else {
            vec!["sorryAx".to_owned()]
        };
        TheoremCheck {
            name: name.to_owned(),
            module: String::new(),
            id: None,
            title: None,
            function: None,
            issues: Vec::new(),
            axioms: Some(axioms),
            drops_writes: Some(false),
        }
    }

    /// The theorems under `Spec/`: the rule theorem, the base case and a lemma
    /// are proven, and the induction step is not.
    pub(crate) fn spec_theorems() -> Vec<TheoremCheck> {
        vec![
            labelled("Spec.FAdds.f_adds", 1, "module.Struct.f", true),
            labelled("Spec.Inv.new", 2, "module.Struct.new", true),
            labelled("Spec.Inv.f", 2, "module.Struct.f", false),
            unlabelled("Spec.InvAux.lemma", true),
        ]
    }

    /// A submission that matches `spec_theorems`.
    pub(crate) fn submission() -> ProveSubmission {
        ProveSubmission {
            theorems: BTreeMap::from([
                (
                    "Spec.FAdds.f_adds".to_owned(),
                    TheoremStatus::ProvenWithAssumptions {
                        assumptions: vec![AdditionalAssumption {
                            name: "h_ext".to_owned(),
                            reason: String::new(),
                            purpose: AssumptionPurpose::External {
                                justification: String::new(),
                            },
                        }],
                    },
                ),
                ("Spec.Inv.new".to_owned(), TheoremStatus::Proven),
                (
                    "Spec.Inv.f".to_owned(),
                    TheoremStatus::Refuted {
                        reason: String::new(),
                    },
                ),
            ]),
            dropped_writes: BTreeMap::new(),
        }
    }
}
