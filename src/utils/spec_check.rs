//! Runs `Lusterna.Spec.check` on the theorems under `Spec/` of a Lean
//! project.

use std::{
    fs,
    io::{self, Write},
    path::Path,
};

use serde::{Deserialize, Serialize};

use crate::{
    stage::properties::PropertyId,
    utils::lean::{self, LakeError},
};

#[derive(Debug, thiserror::Error)]
pub enum SpecCheckError {
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("Lake error: {0}")]
    Lake(#[from] LakeError),
    #[error("cannot parse the output of `Lusterna.Spec.check`: {0}")]
    Parse(#[from] serde_json::Error),
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

/// What `Lusterna.Spec.check` prints for one theorem.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TheoremCheck {
    pub name: String,
    pub module: String,
    pub id: Option<PropertyId>,
    pub title: Option<String>,
    /// The head constant of the computation in the theorem's triple.
    pub function: Option<String>,
    pub issues: Vec<Issue>,
    /// The axioms that leave the theorem unproven: empty if it is proven.
    /// `None` if the check ran without `axioms`.
    pub axioms: Option<Vec<String>>,
    /// The theorem depends on `Lusterna.refMutWriteDropped`: a write through
    /// a `core.cell.RefMut` that it goes through is dropped. `None` if the
    /// check ran without `axioms`.
    pub drops_writes: Option<bool>,
}

/// Runs `Lusterna.Spec.check` on the modules under `Spec/` of the Lean project
/// at `lean_root`. With `axioms`, each result has the axioms of its theorem.
pub fn run(lean_root: &Path, axioms: bool) -> Result<Vec<TheoremCheck>, SpecCheckError> {
    let modules = spec_modules(&lean_root.join("Spec"), "Spec")?;
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
        "import LusternaSpec\n{imports}#eval Lusterna.Spec.check #[{names}] (axioms := {axioms})\n"
    )?;
    let output = lean::lake_env_lean(lean_root, driver.path())?;
    output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).map_err(SpecCheckError::from))
        .collect()
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_check_output() {
        let line = r#"{"axioms":["sorryAx"],"dropsWrites":true,"function":"LusternaSpecTest.f","id":4,"issues":[{"canFail":{"reason":"LusternaSpecTest.f r"}}],"module":"LusternaSpecTest.Fixture","name":"LusternaSpecTest.f_fixpoint","title":"f is a fixpoint"}"#;
        let check: TheoremCheck = serde_json::from_str(line).unwrap();
        assert_eq!(check.id, Some(4));
        assert_eq!(
            check.issues,
            vec![Issue::CanFail {
                reason: "LusternaSpecTest.f r".to_owned()
            }]
        );
        assert_eq!(check.axioms, Some(vec!["sorryAx".to_owned()]));
        assert_eq!(check.drops_writes, Some(true));

        let line = r#"{"axioms":null,"dropsWrites":null,"function":null,"id":null,"issues":["notATriple","ignoresResult","vacuous"],"module":"M","name":"M.two","title":null}"#;
        let check: TheoremCheck = serde_json::from_str(line).unwrap();
        assert_eq!(check.id, None);
        assert_eq!(check.function, None);
        assert_eq!(
            check.issues,
            vec![Issue::NotATriple, Issue::IgnoresResult, Issue::Vacuous]
        );
        assert_eq!(check.axioms, None);
        assert_eq!(check.drops_writes, None);
    }
}
