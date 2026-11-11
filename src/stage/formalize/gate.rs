//! Gate checks of the formalize stage, in order:
//!
//! 1. Only files under `Spec/` differ from `HEAD`.
//! 2. The report has a status for exactly the given properties.
//! 3. `lake build Spec` passes.
//! 4. Every theorem in `Spec/` is `sorry`.
//! 5. No labelled theorem has an issue reported by `Lusterna.Spec.check`.
//! 6. The labels match the report: each label's property is formalized, the
//!    title matches, the theorem is in the reported file, and each property's
//!    labelled theorems are the reported ones.
//! 7. Each formalized invariant has `mutating_operations` for its struct, and
//!    each of them is called by a labelled theorem of that invariant.
//!
//! Labelled theorem: a theorem tagged `@[lusterna id "title"]`, which states
//! the property with that `id` and `title` in the properties file. Untagged
//! theorems are helper lemmas.

use std::{
    collections::{BTreeMap, BTreeSet},
    fmt, fs,
    io::{self, Write},
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    stage::{
        self,
        formalize::{
            FormalizeOutput, FormalizeStage, PropertyStatus,
            properties::{Properties, PropertyId, PropertyKind},
        },
    },
    utils::{
        git::{self, GitError},
        lean::{self, LakeError},
    },
};

#[derive(Debug, thiserror::Error)]
pub enum FormalizeGateError {
    #[error("Error running git: {0}")]
    Git(#[from] GitError),
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("Lake error: {0}")]
    Lake(#[from] LakeError),
    #[error("cannot parse the output of `Lusterna.Spec.check`: {0}")]
    Check(#[from] serde_json::Error),
}

#[derive(Debug, derive_more::Display, Serialize, Deserialize, Clone)]
pub enum FormalizeGateFailure {
    #[display("{} files changed outside Spec/", files.len())]
    ChangedOutsideSpec { files: Vec<PathBuf> },
    #[display("the property statuses of the report do not match the properties")]
    StatusMismatch {
        missing: Vec<PropertyId>,
        unknown: Vec<PropertyId>,
    },
    #[display("lake build Spec failed")]
    BuildFailed { output: String },
    #[display("{} labelled theorems have issues", theorems.len())]
    IssuesFound { theorems: Vec<(String, Vec<Issue>)> },
    #[display("{} labels do not match the report", problems.len())]
    LabelMismatch { problems: Vec<LabelProblem> },
    #[display("no mutating operations reported for {}", structs.join(", "))]
    MissingOperations { structs: Vec<String> },
    #[display("{} mutating operations have no theorem", missing.len())]
    UncoveredOperations { missing: Vec<(PropertyId, String)> },
}

/// A problem with the statement of a labelled theorem, as
/// `Lusterna.Spec.check` reports it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, derive_more::Display)]
#[serde(rename_all = "camelCase")]
pub enum Issue {
    #[display("the conclusion is not an Aeneas WP triple")]
    NotATriple,
    #[display("the postcondition does not mention the result")]
    IgnoresResult,
    #[display("the postcondition contains a computation that can fail: {reason}")]
    CanFail { reason: String },
    #[display("the postcondition holds for every result")]
    Vacuous,
}

/// A mismatch between the `@[lusterna]` labels in `Spec/` and the report.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum LabelProblem {
    /// `theorem` is labelled with `id`, but the report does not mark `id` as
    /// formalized.
    UnreportedLabel { theorem: String, id: PropertyId },
    /// The label of `theorem` does not have `title`, the title of its
    /// property.
    WrongTitle { theorem: String, title: String },
    /// `theorem` is not in `file`, the file that the report gives for its
    /// property.
    WrongFile { theorem: String, file: PathBuf },
    /// The report lists `unlabelled` for property `id`, but they do not carry
    /// its label, and `unreported` carry its label, but the report does not
    /// list them.
    TheoremsDiffer {
        id: PropertyId,
        unlabelled: Vec<String>,
        unreported: Vec<String>,
    },
}

/// What `Lusterna.Spec.check` prints for one theorem.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TheoremCheck {
    name: String,
    module: String,
    id: Option<PropertyId>,
    title: Option<String>,
    /// The head constant of the computation in the theorem's triple.
    function: Option<String>,
    issues: Vec<Issue>,
}

type GateStop = stage::GateStop<FormalizeStage>;

impl From<FormalizeGateFailure> for GateStop {
    fn from(failure: FormalizeGateFailure) -> Self {
        GateStop::Failure(failure)
    }
}

impl<E> From<E> for GateStop
where
    FormalizeGateError: From<E>,
{
    fn from(error: E) -> Self {
        GateStop::Error(error.into())
    }
}

/// Checks the result of the agent:
///
/// - only files under `Spec/` in `lean_root` differ from `HEAD`
/// - the report gives a status for every property, and for no other id
/// - `lake build Spec` succeeds in `lean_root`
/// - the theorems in `Spec/` pass `check_theorems`
pub(super) fn run_gate(
    lean_root: &Path,
    properties: &Properties,
    report: &FormalizeOutput,
) -> Result<(), GateStop> {
    let spec = lean_root.join("Spec").canonicalize()?;

    // Only files under `Spec/` may change
    let repo_root = git::toplevel(lean_root)?;
    let files: Vec<PathBuf> = git::uncommitted_files(lean_root)?
        .into_iter()
        .filter(|f| !repo_root.join(f).starts_with(&spec))
        .collect();
    if !files.is_empty() {
        return Err(FormalizeGateFailure::ChangedOutsideSpec { files }.into());
    }

    // The report must give a status for exactly the properties
    // Missing: properties given but not in the report
    let missing: Vec<PropertyId> = properties
        .0
        .iter()
        .map(|p| p.id)
        .filter(|id| !report.property_status.contains_key(id))
        .collect();
    // Unknown: properties in the report no in the given properties
    let unknown: Vec<PropertyId> = report
        .property_status
        .keys()
        .copied()
        .filter(|id| !properties.0.iter().any(|p| p.id == *id))
        .collect();
    if !missing.is_empty() || !unknown.is_empty() {
        return Err(FormalizeGateFailure::StatusMismatch { missing, unknown }.into());
    }

    // Running `lake build Spec` must succeed in the lean root
    lean::lake_build(lean_root, &["Spec"]).map_err(|e| match e {
        LakeError::Build { output, .. } => FormalizeGateFailure::BuildFailed { output }.into(),
        e => GateStop::from(e),
    })?;

    let checks = run_check(lean_root, &spec_modules(&spec, "Spec")?)?;
    check_theorems(properties, report, &checks)?;
    Ok(())
}

/// Returns the module names of the `.lean` files under `dir`, whose module
/// name is `module`.
fn spec_modules(dir: &Path, module: &str) -> io::Result<Vec<String>> {
    let mut modules = Vec::new();
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            let name = path.file_name().unwrap().to_string_lossy();
            modules.extend(spec_modules(&path, &format!("{module}.{name}"))?);
        } else if path.extension() == Some("lean".as_ref()) {
            let name = path.file_stem().unwrap().to_string_lossy();
            modules.push(format!("{module}.{name}"));
        }
    }
    Ok(modules)
}

/// Runs `Lusterna.Spec.check` on `modules` in the Lean project at
/// `lean_root`.
fn run_check(
    lean_root: &Path,
    modules: &[String],
) -> Result<Vec<TheoremCheck>, FormalizeGateError> {
    if modules.is_empty() {
        return Ok(Vec::new());
    }
    let imports: String = modules.iter().map(|m| format!("import {m}\n")).collect();
    let names = modules
        .iter()
        .map(|m| format!("`{m}"))
        .collect::<Vec<_>>()
        .join(", ");
    let mut driver = tempfile::Builder::new().suffix(".lean").tempfile()?;
    write!(
        driver,
        "import LusternaSpec\n{imports}#eval Lusterna.Spec.check #[{names}]\n"
    )?;
    let output = lean::lake_env_lean(lean_root, driver.path())?;
    output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).map_err(FormalizeGateError::from))
        .collect()
}

/// Checks the theorems in `Spec/` against the properties and the report, and
/// returns the first check that fails.
fn check_theorems(
    properties: &Properties,
    report: &FormalizeOutput,
    checks: &[TheoremCheck],
) -> Result<(), FormalizeGateFailure> {
    check_issues(checks)?;
    check_labels(properties, report, checks)?;
    check_operations(properties, report, checks)
}

/// Checks that no labelled theorem has an issue.
fn check_issues(checks: &[TheoremCheck]) -> Result<(), FormalizeGateFailure> {
    let thms_with_issues: Vec<(String, Vec<Issue>)> = checks
        .iter()
        .filter(|c| c.id.is_some() && !c.issues.is_empty())
        .map(|c| (c.name.clone(), c.issues.clone()))
        .collect();
    if !thms_with_issues.is_empty() {
        return Err(FormalizeGateFailure::IssuesFound {
            theorems: thms_with_issues,
        });
    }
    Ok(())
}

/// Checks that every label names a property that the report marks as
/// formalized, has its title, and is on a theorem in the file that the report
/// gives for it, and that the labelled theorems of each formalized property are
/// the theorems that the report lists for it.
fn check_labels(
    properties: &Properties,
    report: &FormalizeOutput,
    checks: &[TheoremCheck],
) -> Result<(), FormalizeGateFailure> {
    let mut problems = Vec::new();
    let mut labelled: BTreeMap<PropertyId, BTreeSet<&str>> = BTreeMap::new();
    for check in checks {
        // Skip unlabelled theorems (helper lemmas)
        let Some(id) = check.id else {
            continue;
        };
        // The report must mark the label's property as formalized
        let Some(PropertyStatus::Formalized { file, .. }) = report.property_status.get(&id) else {
            problems.push(LabelProblem::UnreportedLabel {
                theorem: check.name.clone(),
                id,
            });
            continue;
        };
        // Collect the labelled theorems per property, for the loop below
        labelled.entry(id).or_default().insert(&check.name);
        // The label must have the title of its property
        if let Some(property) = properties.0.iter().find(|p| p.id == id)
            && check.title.as_ref() != Some(&property.title)
        {
            problems.push(LabelProblem::WrongTitle {
                theorem: check.name.clone(),
                title: property.title.clone(),
            });
        }
        // The theorem must be in the module of the reported file,
        // `Spec/A/B.lean` being `Spec.A.B`
        let file_module = file
            .with_extension("")
            .components()
            .map(|c| c.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join(".");
        if check.module != file_module {
            problems.push(LabelProblem::WrongFile {
                theorem: check.name.clone(),
                file: file.clone(),
            });
        }
    }
    // The labelled theorems of each formalized property must be the reported
    // ones
    for (id, status) in &report.property_status {
        // Skip skipped properties
        let PropertyStatus::Formalized { theorems, .. } = status else {
            continue;
        };
        let reported: BTreeSet<&str> = theorems.iter().map(String::as_str).collect();
        let labelled = labelled.remove(id).unwrap_or_default();
        // Reported theorems without the label, and labelled theorems not
        // reported
        let unlabelled: Vec<String> = reported
            .difference(&labelled)
            .map(|s| s.to_string())
            .collect();
        let unreported: Vec<String> = labelled
            .difference(&reported)
            .map(|s| s.to_string())
            .collect();
        if !unlabelled.is_empty() || !unreported.is_empty() {
            problems.push(LabelProblem::TheoremsDiffer {
                id: *id,
                unlabelled,
                unreported,
            });
        }
    }
    if !problems.is_empty() {
        return Err(FormalizeGateFailure::LabelMismatch { problems });
    }
    Ok(())
}

/// Checks that every formalized invariant property has `mutating_operations` for its
/// struct, and for each of them a labelled theorem whose triple calls it.
fn check_operations(
    properties: &Properties,
    report: &FormalizeOutput,
    checks: &[TheoremCheck],
) -> Result<(), FormalizeGateFailure> {
    let mut structs_without_operations = BTreeSet::new();
    let mut uncovered_operations = Vec::new();
    for property in &properties.0 {
        // Skip rules
        let PropertyKind::Invariant { r#struct } = &property.kind else {
            continue;
        };
        // Skip skipped properties
        let Some(PropertyStatus::Formalized { .. }) = report.property_status.get(&property.id)
        else {
            continue;
        };
        // The report must list the operations of the struct
        let Some(operations) = report.mutating_operations.get(r#struct) else {
            structs_without_operations.insert(r#struct.clone());
            continue;
        };
        // Each operation must be the function of a labelled theorem of the
        // property
        for operation in operations {
            if !checks
                .iter()
                .any(|c| c.id == Some(property.id) && c.function.as_ref() == Some(operation))
            {
                uncovered_operations.push((property.id, operation.clone()));
            }
        }
    }
    if !structs_without_operations.is_empty() {
        return Err(FormalizeGateFailure::MissingOperations {
            structs: structs_without_operations.into_iter().collect(),
        });
    }
    if !uncovered_operations.is_empty() {
        return Err(FormalizeGateFailure::UncoveredOperations {
            missing: uncovered_operations,
        });
    }

    Ok(())
}

impl fmt::Display for LabelProblem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let names = |names: &[String]| {
            names
                .iter()
                .map(|n| format!("`{n}`"))
                .collect::<Vec<_>>()
                .join(", ")
        };
        match self {
            LabelProblem::UnreportedLabel { theorem, id } => write!(
                f,
                "`{theorem}` is labelled with property {id}, which your report does not mark \
                 as `Formalized`"
            ),
            LabelProblem::WrongTitle { theorem, title } => write!(
                f,
                "the label of `{theorem}` does not have the title of its property, \"{title}\""
            ),
            LabelProblem::WrongFile { theorem, file } => write!(
                f,
                "`{theorem}` is not in `{}`, the file that your report gives for its property",
                file.display()
            ),
            LabelProblem::TheoremsDiffer {
                id,
                unlabelled,
                unreported,
            } => {
                write!(f, "property {id}:")?;
                if !unlabelled.is_empty() {
                    write!(
                        f,
                        " your report lists {}, but they do not carry its label;",
                        names(unlabelled)
                    )?;
                }
                if !unreported.is_empty() {
                    write!(
                        f,
                        " {} carry its label, but your report does not list them;",
                        names(unreported)
                    )?;
                }
                Ok(())
            }
        }
    }
}

impl FormalizeGateFailure {
    pub(super) fn to_feedback_prompt(&self) -> String {
        let list = |items: Vec<String>| {
            items
                .iter()
                .map(|i| format!("- {i}"))
                .collect::<Vec<_>>()
                .join("\n")
        };
        let code = |items: &[String]| items.iter().map(|i| format!("`{i}`")).collect::<Vec<_>>();
        match self {
            FormalizeGateFailure::ChangedOutsideSpec { files } => format!(
                "These files differ from the last commit, but you may only change files under \
                 `Spec/`:\n\n{}\n\n\
                 Undo your changes to them: restore a changed file with \
                 `git restore --staged --worktree -- ':/<file>'`, and delete a file that you \
                 created.",
                list(files.iter().map(|f| format!("`{}`", f.display())).collect())
            ),
            FormalizeGateFailure::StatusMismatch { missing, unknown } => {
                let ids = |ids: &[PropertyId]| {
                    ids.iter()
                        .map(|id| id.to_string())
                        .collect::<Vec<_>>()
                        .join(", ")
                };
                let mut prompt = "Your report must give a status for every property in the \
                                  properties file, and for no other id."
                    .to_string();
                if !missing.is_empty() {
                    prompt += &format!("\n\nThese properties have no status: {}.", ids(missing));
                }
                if !unknown.is_empty() {
                    prompt += &format!("\n\nThese ids are not properties: {}.", ids(unknown));
                }
                prompt
            }
            FormalizeGateFailure::BuildFailed { output } => format!(
                "`lake build Spec` failed with this output:\n\n```\n{output}\n```\n\n\
                 Fix the first error and run `lake build Spec` again until it passes.",
            ),
            FormalizeGateFailure::IssuesFound { theorems } => format!(
                "These labelled theorems break the rules of \"The shape of a theorem\":\n\n{}\n\n\
                 The skill `lusterna:aeneas-specs` shows how to fix each problem.",
                list(
                    theorems
                        .iter()
                        .map(|(name, issues)| format!(
                            "`{name}`: {}",
                            issues
                                .iter()
                                .map(Issue::to_string)
                                .collect::<Vec<_>>()
                                .join("; ")
                        ))
                        .collect()
                )
            ),
            FormalizeGateFailure::LabelMismatch { problems } => format!(
                "The `@[lusterna]` labels in `Spec/` do not match your report:\n\n{}\n\n\
                 Fix the labels or the report so that they agree.",
                list(problems.iter().map(LabelProblem::to_string).collect())
            ),
            FormalizeGateFailure::MissingOperations { structs } => format!(
                "Your report has no entry in `mutating_operations` for these structs of invariant \
                 properties:\n\n{}\n\n\
                 Add the full Lean names of the mutating operations of each struct, under \
                 the struct name exactly as the property gives it.",
                list(code(structs))
            ),
            FormalizeGateFailure::UncoveredOperations { missing } => format!(
                "These mutating operations have no labelled theorem of the invariant property \
                 whose \
                 triple calls them directly:\n\n{}\n\n\
                 Write the induction step for each one, as \"Invariants\" in your briefing \
                 describes.",
                list(
                    missing
                        .iter()
                        .map(|(id, operation)| format!("property {id}: `{operation}`"))
                        .collect()
                )
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stage::formalize::properties::Property;

    fn properties() -> Properties {
        Properties(vec![
            Property {
                id: 1,
                kind: PropertyKind::Rule {
                    fns: vec!["vault::deposit".to_owned()],
                },
                title: "Deposit adds".to_owned(),
                natspec: String::new(),
            },
            Property {
                id: 2,
                kind: PropertyKind::Invariant {
                    r#struct: "vault::Vault".to_owned(),
                },
                title: "Vault is solvent".to_owned(),
                natspec: String::new(),
            },
        ])
    }

    fn report() -> FormalizeOutput {
        FormalizeOutput {
            property_status: BTreeMap::from([
                (
                    1,
                    PropertyStatus::Formalized {
                        file: PathBuf::from("Spec/DepositAdds.lean"),
                        theorems: vec!["Spec.DepositAdds.adds".to_owned()],
                    },
                ),
                (
                    2,
                    PropertyStatus::Formalized {
                        file: PathBuf::from("Spec/Solvent.lean"),
                        theorems: vec![
                            "Spec.Solvent.new".to_owned(),
                            "Spec.Solvent.deposit".to_owned(),
                        ],
                    },
                ),
            ]),
            mutating_operations: BTreeMap::from([(
                "vault::Vault".to_owned(),
                vec!["vault.Vault.deposit".to_owned()],
            )]),
            new_theorems: Vec::new(),
            updated_theorems: Vec::new(),
            removed_theorems: Vec::new(),
        }
    }

    fn check(
        name: &str,
        module: &str,
        id: PropertyId,
        title: &str,
        function: &str,
    ) -> TheoremCheck {
        TheoremCheck {
            name: name.to_owned(),
            module: module.to_owned(),
            id: Some(id),
            title: Some(title.to_owned()),
            function: Some(function.to_owned()),
            issues: Vec::new(),
        }
    }

    fn checks() -> Vec<TheoremCheck> {
        vec![
            check(
                "Spec.DepositAdds.adds",
                "Spec.DepositAdds",
                1,
                "Deposit adds",
                "vault.Vault.deposit",
            ),
            check(
                "Spec.Solvent.new",
                "Spec.Solvent",
                2,
                "Vault is solvent",
                "vault.Vault.new",
            ),
            check(
                "Spec.Solvent.deposit",
                "Spec.Solvent",
                2,
                "Vault is solvent",
                "vault.Vault.deposit",
            ),
        ]
    }

    #[test]
    fn parses_check_output() {
        let line = r#"{"function":"LusternaSpecTest.f","id":4,"issues":[{"canFail":{"reason":"LusternaSpecTest.f r"}}],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_fixpoint","title":"f is a fixpoint"}"#;
        let check: TheoremCheck = serde_json::from_str(line).unwrap();
        assert_eq!(check.id, Some(4));
        assert_eq!(
            check.issues,
            vec![Issue::CanFail {
                reason: "LusternaSpecTest.f r".to_owned()
            }]
        );

        let line = r#"{"function":null,"id":null,"issues":["notATriple","ignoresResult","vacuous"],"module":"M","name":"M.two","title":null}"#;
        let check: TheoremCheck = serde_json::from_str(line).unwrap();
        assert_eq!(check.id, None);
        assert_eq!(check.function, None);
        assert_eq!(
            check.issues,
            vec![Issue::NotATriple, Issue::IgnoresResult, Issue::Vacuous]
        );
    }

    #[test]
    fn consistent_report_passes() {
        check_theorems(&properties(), &report(), &checks()).unwrap();
    }

    #[test]
    fn rejects_issue() {
        let mut checks = checks();
        checks[0].issues = vec![Issue::Vacuous];
        assert!(matches!(
            check_theorems(&properties(), &report(), &checks),
            Err(FormalizeGateFailure::IssuesFound { .. })
        ));
    }

    #[test]
    fn rejects_label_mismatches() {
        let mut checks = checks();
        checks[0].title = Some("Other".to_owned());
        checks[1].module = "Spec.Other".to_owned();
        checks.push(check("Spec.X.x", "Spec.X", 3, "X", "f"));
        checks.push(check(
            "Spec.Solvent.extra",
            "Spec.Solvent",
            2,
            "Vault is solvent",
            "f",
        ));
        let Err(FormalizeGateFailure::LabelMismatch { problems }) =
            check_theorems(&properties(), &report(), &checks)
        else {
            panic!("expected a label mismatch");
        };
        assert_eq!(
            problems,
            vec![
                LabelProblem::WrongTitle {
                    theorem: "Spec.DepositAdds.adds".to_owned(),
                    title: "Deposit adds".to_owned(),
                },
                LabelProblem::WrongFile {
                    theorem: "Spec.Solvent.new".to_owned(),
                    file: PathBuf::from("Spec/Solvent.lean"),
                },
                LabelProblem::UnreportedLabel {
                    theorem: "Spec.X.x".to_owned(),
                    id: 3,
                },
                LabelProblem::TheoremsDiffer {
                    id: 2,
                    unlabelled: Vec::new(),
                    unreported: vec!["Spec.Solvent.extra".to_owned()],
                },
            ]
        );
    }

    #[test]
    fn rejects_missing_operations() {
        let mut report = report();
        report.mutating_operations.clear();
        assert!(matches!(
            check_theorems(&properties(), &report, &checks()),
            Err(FormalizeGateFailure::MissingOperations { structs }) if structs == ["vault::Vault"]
        ));
    }

    #[test]
    fn rejects_uncovered_operation() {
        let mut report = report();
        report
            .mutating_operations
            .get_mut("vault::Vault")
            .unwrap()
            .push("vault.Vault.withdraw".to_owned());
        assert!(matches!(
            check_theorems(&properties(), &report, &checks()),
            Err(FormalizeGateFailure::UncoveredOperations { missing })
                if missing == [(2, "vault.Vault.withdraw".to_owned())]
        ));
    }
}
