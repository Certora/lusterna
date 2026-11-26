mod formalize;
mod pipeline;
mod pre_translate;
mod prove;
mod translate;

use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

use lusterna::stage::{
    formalize::{FormalizeSubmission, PropertyFormalizationStatus},
    properties::PropertyId,
};
use serde::de::DeserializeOwned;

// The ids of the properties in `tests/fixtures/demo/lusterna.json`
const CHECKED_TOTAL: PropertyId = 1;
const LOG_BUCKET_NOT_ZERO: PropertyId = 2;
const VAULT_SHARES: PropertyId = 3;
const UNREACHABLE: PropertyId = 4;
const MUL_DIV: PropertyId = 5;

fn fixture() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/demo")
}

fn run_dir() -> PathBuf {
    env::var_os("LUSTERNA_IT_RUN_DIR")
        .map(PathBuf::from)
        .expect("`LUSTERNA_IT_RUN_DIR` is not set; run `just integration-test`")
}

fn lean_root() -> PathBuf {
    run_dir().join("lusterna/lean")
}

/// The submission of `stage`, from `lusterna/<stage>.json`
fn submission<T: DeserializeOwned>(stage: &str) -> T {
    let path = run_dir().join(format!("lusterna/{stage}.json"));
    serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap()
}

/// Runs `git <args>` in the run directory and returns its stdout.
fn git(args: &[&str]) -> String {
    let output = Command::new("git")
        .arg("-C")
        .arg(run_dir())
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap()
}

/// The hash of the commit with the subject `subject`
fn commit_with_subject(subject: &str) -> String {
    let log = git(&["log", "--format=%H %s"]);
    log.lines()
        .find_map(|line| {
            let (hash, line_subject) = line.split_once(' ')?;
            (line_subject == subject).then(|| hash.to_owned())
        })
        .unwrap_or_else(|| panic!("no commit `{subject}`"))
}

/// The file and the theorems of the formalized property `id`
fn formalized(formalize: &FormalizeSubmission, id: PropertyId) -> (PathBuf, Vec<String>) {
    match &formalize.property_status[&id] {
        PropertyFormalizationStatus::Formalized { file, theorems, .. } => {
            (file.clone(), theorems.clone())
        }
        PropertyFormalizationStatus::Skipped(reason) => {
            panic!("property {id} is skipped: {reason:?}")
        }
    }
}

/// The declaration of the theorem `name` in `text`, the content of the file
/// `file` under the Lean root, up to the next declaration
fn theorem_block(text: &str, file: &Path, name: &str) -> String {
    let namespace = format!("Spec.{}.", file.file_stem().unwrap().to_str().unwrap());
    let short = name.strip_prefix(&namespace).unwrap_or(name);
    let start = text
        .find(&format!("theorem {short} "))
        .or_else(|| text.find(&format!("theorem {short}\n")))
        .unwrap_or_else(|| panic!("no theorem `{name}` in {}", file.display()));
    let rest = &text[start..];
    let mut end = rest.len();
    for (offset, line) in rest.match_indices('\n') {
        let next = &rest[offset + line.len()..];
        if offset > 0
            && [
                "theorem ",
                "@[",
                "lemma ",
                "def ",
                "end ",
                "/--",
                "namespace ",
            ]
            .iter()
            .any(|keyword| next.starts_with(keyword))
        {
            end = offset;
            break;
        }
    }
    rest[..end].to_owned()
}
