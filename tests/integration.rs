use std::{env, path::PathBuf};

fn run_dir() -> PathBuf {
    env::var_os("LUSTERNA_IT_RUN_DIR")
        .map(PathBuf::from)
        .expect("`LUSTERNA_IT_RUN_DIR` is not set; run `just integration-test`")
}

#[test]
#[ignore = "needs the output of `just integration-test`"]
fn run_writes_the_report() {
    assert!(run_dir().join("lusterna/report.md").is_file());
}
