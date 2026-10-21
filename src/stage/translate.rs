use std::{
    fs, io,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

use serde::Serialize;

use crate::{
    stage::Stage,
    utils::{
        cargo::{self, CargoError},
        lean::{self, LakeError},
    },
};

/// The agent briefing of the translate stage, with `{crate_root}`,
/// `{entrypoints}` and `{opaque}` standing for the crate root and the
/// Markdown lists of the Charon `--start-from` and `--opaque` patterns.
const BRIEFING_TEMPLATE: &'static str = r#"You are the translate stage of Lusterna. Your task is to make Charon and Aeneas translate the Rust crate at `{crate_root}` to Lean. You do this by changing the Rust code where the tools fail, without changing what the code does.

## What is already set up

- `lusterna/extract.sh` runs Charon and then Aeneas on the crate, with the feature `lusterna` enabled. Aeneas writes the Lean code to `lusterna/lean/Extraction.lean`.
- `lusterna/lean` is a Lake project that depends on the Aeneas Lean library.
- The crate's `Cargo.toml` has an empty feature `lusterna`.

Charon starts the translation from these entrypoints:
{entrypoints}

Charon leaves these items opaque:
{opaque}

## When you are done

The harness checks these conditions after you stop. If one fails, you get the error and must continue.

1. `lusterna/extract.sh` exits with status 0.
2. `lake build` in `lusterna/lean` exits with status 0.
3. In `extract.sh`, the `ENTRYPOINTS=` and `OPAQUE=` lines and the two `for` loops are unchanged, no other line passes `--start-from` or `--opaque`, and the `charon` command passes `"${args[@]}"`.
4. `cargo build` without the `lusterna` feature succeeds.
5. `cargo test lusterna_test` without the `lusterna` feature passes within 10 minutes. If the crate uses `feature = "lusterna"`, it must run at least one test.

## How to work

1. Run `lusterna/extract.sh`. If it passes and `lake build` passes, stop. Do not change anything.
2. Otherwise, read the first error and fix it with the smallest change that works. Run the script again. Repeat until it passes.
3. Run `lake build` in `lusterna/lean` and fix the errors that come from your changes.
4. Run `cargo test lusterna_test` and make sure that your equivalence tests pass. Other tests of the crate can fail already before your changes, and you do not need to fix them.

## How to change the code

The code that the crate builds without the `lusterna` feature must stay exactly as it is. Put every change behind `#[cfg(feature = "lusterna")]`. Keep the original code compiled in test builds, so the equivalence tests can call both versions.

If Charon or Aeneas cannot translate a piece of code, write a replacement that computes the same result with constructs they support. Replace only the part that fails, not the whole function. For example:

```rust
#[cfg(not(feature = "lusterna"))]
pub fn total(v: &[u64]) -> u64 {
    v.iter().sum()
}

#[cfg(feature = "lusterna")]
pub fn total(v: &[u64]) -> u64 {
    total_lusterna(v)
}

#[cfg(any(test, feature = "lusterna"))]
fn total_lusterna(v: &[u64]) -> u64 {
    let mut sum = 0;
    let mut i = 0;
    while i < v.len() {
        sum += v[i];
        i += 1;
    }
    sum
}
```

If Charon or Aeneas cannot translate a type, add a mock type with the same name behind `#[cfg(feature = "lusterna")]`. The mock must keep the values and operations that the entrypoints use, and nothing else.

A replacement must behave exactly like the original for every input: the same results, the same panics, and the same overflow behavior. For example, do not replace `+` with `wrapping_add`.

Common constructs that Aeneas cannot translate: iterator adaptor chains, closures, `Option` and `Result` combinators such as `map` and `and_then`, and globals of function type.
This list is just a heuristic and neither a ground truth nor exhaustive, it depends on the Aeneas version you run against. You must evaluate errors case by case. 

## Equivalence tests

For every replacement, add a test in a `#[cfg(test)] mod lusterna_test` next to it. The harness runs only the tests whose path contains `lusterna_test`. The test calls the original and the replacement with the same inputs and compares the results. Use many random inputs and add the edge cases: zero, the maximum value, values that overflow, and empty collections. If a dependency for random inputs is not available, use a loop over a simple seeded generator.

Run the tests with `cargo test lusterna_test`, without the `lusterna` feature, so that the original code is the real one.

## What you must not do

- Do not change the entrypoints or the opaque patterns. If a pattern matches nothing, stop and say so.
- Do not use `--exclude` or a new `--opaque` to hide code that the entrypoints reach.
- Do not edit `lusterna/lean/lakefile.toml`, `lusterna/lean/lean-toolchain` or the generated Lean code.
- Do not change code that the crate builds without the `lusterna` feature.

You can change other Charon and Aeneas options in `extract.sh` if that helps the translation.

## Your final message

List every change you made. For each change, give the file, what you replaced, why Charon or Aeneas failed on the original, and the name of its equivalence test.
"#;

/// The first prompt of the translate stage, with `{crate_root}` standing for
/// the crate root and `{script_state}` for a sentence on whether
/// `lusterna/extract.sh` existed before setup.
const BASE_PROMPT_TEMPLATE: &'static str = r#"Start the translate stage for the crate at `{crate_root}`.

{script_state}

Begin by running `lusterna/extract.sh` from the crate root."#;

pub struct TranslateInput {
    /// Absolute path to the workspace of the Rust project.
    pub workspace_root: PathBuf,
    /// If the workspace has more than one crate, this must the absolute path to
    /// the crate. In that case, `workspace_root` must be a parent of this
    /// path. If this is empty, then the stage assumes the `workspace_root`
    /// is also the `crate` root
    pub crate_root: Option<PathBuf>,

    /// The entrypoint pattern passed to Aeneas/Charon to be translated
    pub entrypoints: Vec<String>,

    /// The opaque pattern passed to Aeneas/Charon to left opaque
    pub opaque: Vec<String>,

    /// The Aeneas git revision the Lean project depends on
    pub aeneas_rev: String,

    /// The Lean toolchain of that Aeneas revision, as in its
    /// `backends/lean/lean-toolchain`
    pub lean_toolchain: String,
}

impl TranslateInput {
    /// Returns `crate_root` if given, and `workspace_root` otherwise.
    pub fn crate_root(&self) -> &Path {
        self.crate_root.as_deref().unwrap_or(&self.workspace_root)
    }
}

pub struct TranslateStage {
    extraction_existed: bool,
    extraction_script: Option<PathBuf>,
    lean_root: Option<PathBuf>,

    input: TranslateInput,
}

impl TranslateStage {
    /// Constructs a new stage from the given input.
    ///
    /// Must not have side effects.
    pub fn new(input: TranslateInput) -> Self {
        Self {
            extraction_existed: false,
            extraction_script: None,
            lean_root: None,
            input,
        }
    }
}

#[derive(Serialize)]
pub struct TranslateOutput {
    // Absolute path of the crate's root
    pub crate_root: PathBuf,
    /// Absolute path to the extraction script
    pub extract_script: PathBuf,
    /// Absolute path to the Aeneas generated extraction
    pub extracted: PathBuf,
    /// Absolute path to the lean project root dir
    pub lean_root: PathBuf,
    /// The summary of the agent that passed the checks.
    pub agent_summary: String,
}

#[derive(Debug, thiserror::Error)]
pub enum TranslateSetupErr {
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("`{0}` is not the root of a Cargo workspace")]
    NotWorkspaceRoot(PathBuf),
    #[error("`{crate_root}` is not a crate in the workspace `{workspace_root}`")]
    NotWorkspaceMember {
        crate_root: PathBuf,
        workspace_root: PathBuf,
    },
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error(transparent)]
    Lake(#[from] LakeError),
}

#[derive(Debug, thiserror::Error)]
pub enum TranslateGateErr {
    #[error("cannot read `{script}`: {source}")]
    ReadScript {
        script: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("`{script}` must contain this line unchanged: {line}")]
    MissingLine { script: PathBuf, line: String },
    #[error(
        "`{script}` must not set ENTRYPOINTS or OPAQUE or pass --start-from or --opaque here: {line}"
    )]
    UnexpectedLine { script: PathBuf, line: String },
    #[error("the `charon` command in `{script}` must pass \"${{args[@]}}\"")]
    ArgsNotPassed { script: PathBuf },
    #[error("cannot run `{script}`: {source}")]
    RunScript {
        script: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("`{script}` failed:\n{stderr}")]
    ScriptFailed { script: PathBuf, stderr: String },
    #[error(transparent)]
    Lake(#[from] LakeError),
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error("cannot read `{path}`: {source}")]
    ReadSource {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error(
        "`{crate_root}` uses `feature = \"lusterna\"`, but `cargo test lusterna_test` ran no tests"
    )]
    NoEquivalenceTests { crate_root: PathBuf },
}

/// How long `cargo test lusterna_test` may run in the gate check.
const EQUIV_TEST_TIMEOUT: Duration = Duration::from_secs(10 * 60);

/// The extraction script `lusterna/extract.sh`, with `{entrypoints}` and
/// `{opaque}` standing for the bash-quoted patterns.
const EXTRACT_SH_TEMPLATE: &str = r#"#!/usr/bin/env bash
set -eo pipefail

ENTRYPOINTS=({entrypoints})
OPAQUE=({opaque})

cd "$(dirname "$0")/.."

args=()
for p in "${ENTRYPOINTS[@]}"; do args+=(--start-from "$p"); done
for p in "${OPAQUE[@]}"; do args+=(--opaque "$p"); done

charon cargo --preset=aeneas "${args[@]}" --dest-file "$PWD/lusterna/extraction.llbc" -- --features lusterna
aeneas -backend lean -dest lusterna/lean lusterna/extraction.llbc
"#;

/// The `lakefile.toml` of the Lean project, with `{rev}` standing for the
/// Aeneas git revision. Aeneas names the generated module after the `.llbc`
/// file in CamelCase, and `extract.sh` writes `extraction.llbc`.
const LAKEFILE_TEMPLATE: &str = r#"name = "Extraction"
defaultTargets = ["Extraction"]

[[require]]
name = "aeneas"
git = "https://github.com/AeneasVerif/aeneas.git"
rev = "{rev}"
subDir = "backends/lean"

[[lean_lib]]
name = "Extraction"
"#;

/// Returns the content of `lusterna/extract.sh` for the given Charon
/// `--start-from` and `--opaque` patterns.
fn make_extraction_script(entrypoints: &[String], opaque: &[String]) -> String {
    let quote = |patterns: &[String]| {
        patterns
            .iter()
            .map(|p| format!("'{}'", p.replace('\'', r"'\''")))
            .collect::<Vec<_>>()
            .join(" ")
    };
    EXTRACT_SH_TEMPLATE
        .replace("{entrypoints}", &quote(entrypoints))
        .replace("{opaque}", &quote(opaque))
}

/// Returns the content of the Lean project's `lakefile.toml` for the given
/// Aeneas git revision.
fn make_lakefile(aeneas_rev: &str) -> String {
    LAKEFILE_TEMPLATE.replace("{rev}", aeneas_rev)
}

/// Returns whether a `.rs` file in `dir` or its subdirectories contains
/// `feature = "lusterna"`.
fn uses_lusterna_feature(dir: &Path) -> Result<bool, TranslateGateErr> {
    let read_err = |path: &Path| {
        let path = path.to_owned();
        move |source| TranslateGateErr::ReadSource { path, source }
    };
    for entry in fs::read_dir(dir).map_err(read_err(dir))? {
        let path = entry.map_err(read_err(dir))?.path();
        if path.is_dir() {
            if uses_lusterna_feature(&path)? {
                return Ok(true);
            }
        } else if path.extension().is_some_and(|e| e == "rs") {
            let content = fs::read_to_string(&path).map_err(read_err(&path))?;
            if content.contains(r#"feature = "lusterna""#) {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

/// Returns the first prompt for the crate at `crate_root`. `script_existed`
/// tells whether `lusterna/extract.sh` existed before setup.
fn make_base_prompt(crate_root: &Path, script_existed: bool) -> String {
    let script_state = if script_existed {
        "`lusterna/extract.sh` and the changes in the crate come from an earlier run. \
         Keep the earlier changes that are still needed and continue from there."
    } else {
        "Setup created `lusterna/extract.sh` for this run. The crate has no earlier changes."
    };
    BASE_PROMPT_TEMPLATE
        .replace("{crate_root}", &crate_root.display().to_string())
        .replace("{script_state}", script_state)
}

/// Returns the agent briefing for the crate at `crate_root` and the given
/// Charon `--start-from` and `--opaque` patterns.
///
/// `crate_root` must not contain `{entrypoints}` or `{opaque}`, and the
/// entrypoints must not contain `{opaque}`, because each value is inserted
/// before the next placeholder is replaced.
fn make_briefing(crate_root: &Path, entrypoints: &[String], opaque: &[String]) -> String {
    let list = |patterns: &[String]| {
        if patterns.is_empty() {
            return "(none)".to_owned();
        }
        patterns
            .iter()
            .map(|p| format!("- `{p}`"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    BRIEFING_TEMPLATE
        .replace("{crate_root}", &crate_root.display().to_string())
        .replace("{entrypoints}", &list(entrypoints))
        .replace("{opaque}", &list(opaque))
}

/// Checks that the extraction script `script` passes exactly `entrypoints`
/// and `opaque` to Charon. The lines of `make_extraction_script` that set
/// `ENTRYPOINTS` or `OPAQUE` or pass `--start-from` or `--opaque` must appear
/// unchanged, no other line may do so, and the `charon` command must pass
/// `"${args[@]}"`.
fn check_patterns(
    script: &Path,
    entrypoints: &[String],
    opaque: &[String],
) -> Result<(), TranslateGateErr> {
    let content = fs::read_to_string(script).map_err(|source| TranslateGateErr::ReadScript {
        script: script.to_owned(),
        source,
    })?;
    let lines: Vec<&str> = content.lines().map(str::trim).collect();

    let sets_patterns = |line: &str| {
        line.starts_with("ENTRYPOINTS=")
            || line.starts_with("OPAQUE=")
            || line.contains("--start-from")
            || line.contains("--opaque")
    };
    let expected = make_extraction_script(entrypoints, opaque);
    let expected_lines: Vec<&str> = expected.lines().filter(|l| sets_patterns(l)).collect();

    if let Some(line) = expected_lines.iter().find(|l| !lines.contains(l)) {
        return Err(TranslateGateErr::MissingLine {
            script: script.to_owned(),
            line: line.to_string(),
        });
    }
    if let Some(line) = lines
        .iter()
        .find(|l| sets_patterns(l) && !expected_lines.contains(l))
    {
        return Err(TranslateGateErr::UnexpectedLine {
            script: script.to_owned(),
            line: line.to_string(),
        });
    }
    if !lines
        .iter()
        .any(|l| l.starts_with("charon ") && l.contains(r#""${args[@]}""#))
    {
        return Err(TranslateGateErr::ArgsNotPassed {
            script: script.to_owned(),
        });
    }
    Ok(())
}

/// Runs the extraction script `script` and fails with its stderr if it exits
/// with a non-zero status.
fn run_script(script: &Path) -> Result<(), TranslateGateErr> {
    let output = Command::new(script)
        .output()
        .map_err(|source| TranslateGateErr::RunScript {
            script: script.to_owned(),
            source,
        })?;
    if !output.status.success() {
        return Err(TranslateGateErr::ScriptFailed {
            script: script.to_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    Ok(())
}

impl TranslateStage {
    /// Creates the initial extraction script.
    /// Returns a tuple with the first element the path to the script and the
    /// second a flag indicating whether the script was created or already
    /// existed: `true` if it was created and `false` if it already existed.
    /// Returns an error if creation failed
    ///
    /// TODO: this fails if the existing script uses different entry points than
    /// the one we specified here
    fn create_extraction_script(&self) -> Result<(PathBuf, bool), io::Error> {
        let crate_root = self.input.crate_root();
        let path = crate_root.join("lusterna").join("extract.sh");
        if path.exists() {
            return Ok((path, false));
        }

        fs::create_dir_all(path.parent().unwrap())?;
        fs::write(
            &path,
            make_extraction_script(&self.input.entrypoints, &self.input.opaque),
        )?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755))?;
        Ok((path, true))
    }

    /// Creates the lean project in `lusterna/lean` of the crate root:
    /// - writes a `lakefile.toml`
    /// - sets up the dependency to Aeneas for the needed ref
    /// - wires the extraction file(s) (possibly not existing yet) to be part of
    ///   the project
    /// - writes the `lean-toolchain` and calls `lake update`
    ///
    /// Returns a tuple with the first element the path to the lean project root
    /// and the second a flag indicating whether a project was actually
    /// initialized: `true` if it was and `false` if it already existed.
    /// Returns an error if failed.
    ///
    /// TODO: this fails if the existing lakefile states other dependencies
    /// or a different Aeneas version.
    fn init_lake_project(&self) -> Result<(PathBuf, bool), TranslateSetupErr> {
        let crate_root = self.input.crate_root();
        let lean_root = crate_root.join("lusterna").join("lean");
        if lean_root.join("lakefile.toml").exists() {
            return Ok((lean_root, false));
        }

        fs::create_dir_all(&lean_root)?;
        fs::write(
            lean_root.join("lakefile.toml"),
            make_lakefile(&self.input.aeneas_rev),
        )?;
        fs::write(
            lean_root.join("lean-toolchain"),
            format!("{}\n", self.input.lean_toolchain.trim()),
        )?;

        lean::lake_update(&lean_root)?;

        Ok((lean_root, true))
    }
}

impl Stage for TranslateStage {
    type Input = TranslateInput;

    type Output = TranslateOutput;

    type SetupError = TranslateSetupErr;

    type GateError = TranslateGateErr;

    fn name() -> &'static str {
        "translate"
    }

    fn setup(&mut self) -> Result<(), Self::SetupError> {
        // Check if workspace root is actually a rust workspace
        let workspace_root = &self.input.workspace_root;
        let ws_manifest = fs::canonicalize(workspace_root.join("Cargo.toml"))
            .map_err(|_| TranslateSetupErr::NotWorkspaceRoot(workspace_root.clone()))?;
        if fs::canonicalize(cargo::workspace_manifest(&ws_manifest)?)? != ws_manifest {
            return Err(TranslateSetupErr::NotWorkspaceRoot(workspace_root.clone()));
        }

        // If crate root given: Check if it is a crate within the workspace and that it
        // compiles/build
        // If crate root not given: Check that the workspace is a crate root and
        // that it compiles/builds
        let crate_root = self.input.crate_root();
        let crate_manifest = crate_root.join("Cargo.toml");
        if !crate_manifest.exists()
            || fs::canonicalize(cargo::workspace_manifest(&crate_manifest)?)? != ws_manifest
        {
            return Err(TranslateSetupErr::NotWorkspaceMember {
                crate_root: crate_root.to_owned(),
                workspace_root: workspace_root.clone(),
            });
        }
        cargo::build(&crate_manifest)?;
        cargo::add_feature(&crate_manifest, "lusterna")?;

        let (path, created) = self.create_extraction_script()?;
        self.extraction_script = Some(path);
        self.extraction_existed = !created;

        let (path, _) = self.init_lake_project()?;
        self.lean_root = Some(path);

        Ok(())
    }

    fn base_prompt(&self) -> String {
        let crate_root = self.input.crate_root();
        make_base_prompt(crate_root, self.extraction_existed)
    }

    fn feedback_prompt(gate_error: &Self::GateError) -> String {
        match gate_error {
            TranslateGateErr::ReadScript { script, source } => format!(
                "The harness cannot read `{}`: {source}\n\n\
                 Make sure that the file exists and is readable. Do not delete or move it.",
                script.display()
            ),
            TranslateGateErr::MissingLine { script, line } => format!(
                "`{}` must contain this line unchanged:\n\n```\n{line}\n```\n\n\
                 Restore the line. You must not change the entrypoints, the opaque patterns \
                 or the loops that turn them into Charon options.",
                script.display()
            ),
            TranslateGateErr::UnexpectedLine { script, line } => format!(
                "`{}` contains this line, which sets `ENTRYPOINTS` or `OPAQUE` or passes \
                 `--start-from` or `--opaque`:\n\n```\n{line}\n```\n\n\
                 Remove it. Only the lines that setup wrote can set these patterns.",
                script.display()
            ),
            TranslateGateErr::ArgsNotPassed { script } => format!(
                "The `charon` command in `{}` does not pass `\"${{args[@]}}\"`.\n\n\
                 Add it back, so that Charon receives the entrypoints and the opaque patterns.",
                script.display()
            ),
            TranslateGateErr::RunScript { script, source } => format!(
                "The harness cannot run `{}`: {source}\n\n\
                 Make sure that the file exists, is executable and starts with \
                 `#!/usr/bin/env bash`.",
                script.display()
            ),
            TranslateGateErr::ScriptFailed { script, stderr } => format!(
                "`{}` failed with this output:\n\n```\n{stderr}\n```\n\n\
                 Fix the first error and run the script again until it passes.",
                script.display()
            ),
            TranslateGateErr::Lake(LakeError::Build { lean_root, output }) => format!(
                "`lake build` in `{}` failed with this output:\n\n```\n{output}\n```\n\n\
                 Fix the Rust code so that Aeneas generates Lean code that builds. \
                 Do not edit the generated Lean code.",
                lean_root.display()
            ),
            TranslateGateErr::Lake(lake_error) => format!(
                "The harness failed to run Lake: {lake_error}\n\n\
                 This is not caused by your changes. Stop and report the error."
            ),
            TranslateGateErr::Cargo(CargoError::Build { manifest, stderr }) => format!(
                "`cargo build` without the `lusterna` feature failed for `{}`:\n\n\
                 ```\n{stderr}\n```\n\n\
                 Your changes must not change the code that the crate builds without the \
                 feature. Fix the `cfg` attributes so that this build works again.",
                manifest.display()
            ),
            TranslateGateErr::Cargo(CargoError::Test {
                manifest,
                filter,
                stdout,
                stderr,
            }) => format!(
                "`cargo test {filter}` failed for `{}`:\n\n```\n{stdout}\n{stderr}\n```\n\n\
                 A failing equivalence test means that a replacement does not behave like the \
                 original. Fix the replacement, not the test, unless the test itself is wrong.",
                manifest.display()
            ),
            TranslateGateErr::Cargo(CargoError::TestTimeout {
                manifest,
                filter,
                timeout,
            }) => format!(
                "`cargo test {filter}` for `{}` did not finish within {timeout:?}.\n\n\
                 Make the equivalence tests faster, for example with fewer random inputs, \
                 and make sure that no test loops forever.",
                manifest.display()
            ),
            TranslateGateErr::Cargo(cargo_error) => format!(
                "The harness failed to run Cargo: {cargo_error}\n\n\
                 This is not caused by your changes. Stop and report the error."
            ),
            TranslateGateErr::ReadSource { path, source } => format!(
                "The harness cannot read `{}`: {source}\n\n\
                 Make sure that the source files of the crate are readable.",
                path.display()
            ),
            TranslateGateErr::NoEquivalenceTests { crate_root } => format!(
                "The crate at `{}` uses `feature = \"lusterna\"`, but `cargo test lusterna_test` \
                 ran no tests.\n\n\
                 Add an equivalence test for every replacement, in a \
                 `#[cfg(test)] mod lusterna_test` next to it.",
                crate_root.display()
            ),
        }
    }

    fn gate_check(&self, agent_summary: String) -> Result<Self::Output, Self::GateError> {
        let script = self
            .extraction_script
            .as_deref()
            .expect("setup must run before gate_check");
        let lean_root = self
            .lean_root
            .as_deref()
            .expect("setup must run before gate_check");

        // The entrypoints and opaque patterns must match the given inputs
        check_patterns(script, &self.input.entrypoints, &self.input.opaque)?;

        // Running the extraction script must succeed
        run_script(script)?;

        // Running `lake build` must succeed in the lean root
        lean::lake_build(lean_root)?;

        // The crate must build without the `lusterna` feature
        let crate_root = self.input.crate_root();
        let manifest = crate_root.join("Cargo.toml");
        cargo::build(&manifest)?;

        // The equivalence tests must pass, and must exist if the crate uses
        // the `lusterna` feature
        let passed = cargo::test(&manifest, "lusterna_test", EQUIV_TEST_TIMEOUT)?;
        if passed == 0 && uses_lusterna_feature(&crate_root.join("src"))? {
            return Err(TranslateGateErr::NoEquivalenceTests {
                crate_root: crate_root.to_owned(),
            });
        }

        Ok(TranslateOutput {
            crate_root: crate_root.to_owned(),
            extract_script: script.to_owned(),
            extracted: lean_root.join("Extraction.lean"),
            lean_root: lean_root.to_owned(),
            agent_summary,
        })
    }

    fn briefing(&self) -> String {
        let crate_root = self.input.crate_root();
        make_briefing(crate_root, &self.input.entrypoints, &self.input.opaque)
    }
}

#[cfg(test)]
mod tests {
    use std::process::Command;

    use toml_edit::DocumentMut;

    use super::*;

    /// Runs the part of `script` before the `cd` line in bash and returns the
    /// elements of the bash array `array`.
    fn bash_array(script: &str, array: &str) -> Vec<String> {
        let (definitions, _) = script.split_once("\ncd ").unwrap();
        let output = Command::new("bash")
            .arg("-c")
            .arg(format!(
                "{definitions}\nfor x in \"${{{array}[@]}}\"; do printf '%s\\0' \"$x\"; done"
            ))
            .output()
            .unwrap();
        assert!(output.status.success(), "{output:?}");
        String::from_utf8(output.stdout)
            .unwrap()
            .split_terminator('\0')
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn extract_sh_keeps_patterns_intact() {
        let entrypoints = vec![
            "crate::f".to_owned(),
            "crate::{impl Foo for Bar}::g".to_owned(),
            "it's $HOME `x` \"y\"".to_owned(),
        ];
        let opaque = vec!["crate::a b".to_owned(), "'".to_owned()];

        let script = make_extraction_script(&entrypoints, &opaque);

        assert_eq!(bash_array(&script, "ENTRYPOINTS"), entrypoints);
        assert_eq!(bash_array(&script, "OPAQUE"), opaque);
    }

    #[test]
    fn extract_sh_allows_empty_patterns() {
        let script = make_extraction_script(&[], &[]);

        assert!(bash_array(&script, "ENTRYPOINTS").is_empty());
        assert!(bash_array(&script, "OPAQUE").is_empty());
    }

    #[test]
    fn lakefile_requires_aeneas_at_rev() {
        let doc: DocumentMut = make_lakefile("abc123").parse().unwrap();

        let aeneas = &doc["require"][0];
        assert_eq!(aeneas["name"].as_str(), Some("aeneas"));
        assert_eq!(aeneas["rev"].as_str(), Some("abc123"));
        assert_eq!(aeneas["subDir"].as_str(), Some("backends/lean"));
    }

    /// Writes `content` as an executable script into `dir` and returns its
    /// path.
    fn write_script(dir: &Path, content: &str) -> PathBuf {
        let path = dir.join("extract.sh");
        fs::write(&path, content).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    fn patterns() -> (Vec<String>, Vec<String>) {
        (
            vec!["crate::f".to_owned(), "crate::{impl A for B}::g".to_owned()],
            vec!["crate::h".to_owned()],
        )
    }

    #[test]
    fn check_patterns_accepts_generated_script() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let script = write_script(dir.path(), &make_extraction_script(&entrypoints, &opaque));

        check_patterns(&script, &entrypoints, &opaque).unwrap();
    }

    #[test]
    fn check_patterns_accepts_other_charon_options() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content = make_extraction_script(&entrypoints, &opaque).replace(
            "charon cargo --preset=aeneas",
            "charon cargo --preset=aeneas --hide-marker-traits",
        );
        let script = write_script(dir.path(), &content);

        check_patterns(&script, &entrypoints, &opaque).unwrap();
    }

    #[test]
    fn check_patterns_rejects_changed_entrypoints() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let script = write_script(
            dir.path(),
            &make_extraction_script(&["crate::other".to_owned()], &opaque),
        );

        match check_patterns(&script, &entrypoints, &opaque) {
            Err(TranslateGateErr::MissingLine { line, .. }) => {
                assert!(line.starts_with("ENTRYPOINTS="), "{line}")
            }
            other => panic!("expected TranslateGateErr::MissingLine, got {other:?}"),
        }
    }

    #[test]
    fn check_patterns_rejects_extra_opaque_option() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content = make_extraction_script(&entrypoints, &opaque).replace(
            "charon cargo --preset=aeneas",
            "charon cargo --preset=aeneas --opaque crate::x",
        );
        let script = write_script(dir.path(), &content);

        match check_patterns(&script, &entrypoints, &opaque) {
            Err(TranslateGateErr::UnexpectedLine { line, .. }) => {
                assert!(line.starts_with("charon "), "{line}")
            }
            other => panic!("expected TranslateGateErr::UnexpectedLine, got {other:?}"),
        }
    }

    #[test]
    fn check_patterns_rejects_second_array_assignment() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content =
            make_extraction_script(&entrypoints, &opaque).replace("\ncd ", "\nOPAQUE=()\ncd ");
        let script = write_script(dir.path(), &content);

        match check_patterns(&script, &entrypoints, &opaque) {
            Err(TranslateGateErr::UnexpectedLine { line, .. }) => assert_eq!(line, "OPAQUE=()"),
            other => panic!("expected TranslateGateErr::UnexpectedLine, got {other:?}"),
        }
    }

    #[test]
    fn check_patterns_rejects_charon_without_args() {
        let dir = tempfile::tempdir().unwrap();
        let (entrypoints, opaque) = patterns();
        let content = make_extraction_script(&entrypoints, &opaque).replace(r#""${args[@]}""#, "");
        let script = write_script(dir.path(), &content);

        assert!(matches!(
            check_patterns(&script, &entrypoints, &opaque),
            Err(TranslateGateErr::ArgsNotPassed { .. })
        ));
    }

    #[test]
    fn run_script_succeeds_on_zero_exit() {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(dir.path(), "#!/bin/sh\nexit 0\n");

        run_script(&script).unwrap();
    }

    #[test]
    fn run_script_reports_stderr_on_failure() {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(dir.path(), "#!/bin/sh\necho oops >&2\nexit 1\n");

        match run_script(&script) {
            Err(TranslateGateErr::ScriptFailed { stderr, .. }) => assert_eq!(stderr, "oops\n"),
            other => panic!("expected TranslateGateErr::ScriptFailed, got {other:?}"),
        }
    }
}
