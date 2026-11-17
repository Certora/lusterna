use std::collections::BTreeMap;

use crate::{
    stage::{
        properties::PropertyId,
        prove::{ProveSubmission, TheoremStatus},
    },
    utils::spec_check::TheoremCheck,
};

#[cfg(test)]
pub(super) mod tests {
    use super::*;
    use crate::stage::prove::{AdditionalAssumption, AssumptionPurpose};

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
