use std::fs;

use lusterna::{spec::Spec, stage::properties::PropertyId};

use super::{
    CHECKED_TOTAL, LOG_BUCKET_NOT_ZERO, MUL_DIV, UNREACHABLE, VAULT_SHARES, fixture, git, run_dir,
};

/// The spec of the test crate parses and has the property ids that the tests use.
#[test]
fn spec_of_the_test_crate_parses() {
    let spec: Spec =
        serde_json::from_str(&fs::read_to_string(fixture().join("lusterna.json")).unwrap())
            .unwrap();
    let ids: Vec<PropertyId> = spec.properties.iter().map(|p| p.id).collect();
    assert_eq!(
        ids,
        [CHECKED_TOTAL, LOG_BUCKET_NOT_ZERO, VAULT_SHARES, UNREACHABLE, MUL_DIV]
    );
}

/// Each step of the run commits once, in the order of the pipeline.
#[test]
fn commits_follow_the_pipeline_order() {
    let log = git(&["log", "--reverse", "--format=%s"]);
    let subjects: Vec<&str> = log
        .lines()
        .filter(|s| s.to_lowercase().starts_with("lusterna"))
        .collect();
    assert_eq!(
        subjects,
        [
            "Lusterna: spec",
            "Lusterna: `pre-translate` stage",
            "lusterna: set up translation",
            "Lusterna: `translate` stage",
            "Lusterna: `formalize` stage",
            "Lusterna: `prove` stage",
            "Lusterna: report",
        ]
    );
}

/// The run leaves no uncommitted changes.
#[test]
fn worktree_is_clean_after_the_run() {
    assert_eq!(git(&["status", "--porcelain"]), "");
}

/// The overview table of the report gives each property its expected status.
#[test]
fn report_shows_the_status_of_each_property() {
    let report = fs::read_to_string(run_dir().join("lusterna/report.md")).unwrap();
    let status = |id: PropertyId| -> String {
        let row = report
            .lines()
            .find(|l| l.starts_with(&format!("| {id} | [")))
            .unwrap_or_else(|| panic!("no row for property {id}"));
        row.split('|').nth(4).unwrap().trim().to_owned()
    };
    assert!(status(CHECKED_TOTAL).starts_with("Proven"));
    assert_eq!(status(LOG_BUCKET_NOT_ZERO), "Refuted");
    assert!(status(VAULT_SHARES).starts_with("Proven"));
    assert_eq!(status(UNREACHABLE), "Skipped");
    assert!(status(MUL_DIV).starts_with("Proven"));
}
