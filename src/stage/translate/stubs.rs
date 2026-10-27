use std::path::Path;

use crate::utils::cargo::{self, CargoError};

/// The `lusterna-stubs` checkout.
const LUSTERNA_STUBS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/lusterna-stubs");

/// `lusterna-stubs/stubs.toml`.
const STUBS_TOML: &str = include_str!("../../../lusterna-stubs/stubs.toml");

/// The Lean module `Extraction/Lusterna.lean` of a Solana crate, with
/// `{imports}` standing for the imports of the `lean` modules of the
/// stubs and `{instance}` for the instance of
/// `Lusterna.OverflowSettings`.
const LUSTERNA_LEAN_TEMPLATE: &str = include_str!("res/lusterna.lean.template");

#[derive(Debug, thiserror::Error)]
pub enum StubsError {
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error(
        "the release profile gives `{first}` and `{second}` different overflow settings, but the Lean models take one `Lusterna.OverflowSettings`"
    )]
    DifferentSettings { first: String, second: String },
}

/// A crate that `lusterna-stubs` replaces in the Charon build of a Solana
/// crate.
pub(super) struct Stub {
    /// The name of the crate, and of its stub crate in `lusterna-stubs`.
    pub(super) krate: String,
    /// The `LusternaLib` module with the Lean models of the items of the
    /// crate, if the crate has items in the extraction.
    lean: Option<String>,
}

/// The stubs in `lusterna-stubs/stubs.toml`.
pub(super) fn all() -> Vec<Stub> {
    let doc: toml_edit::DocumentMut = STUBS_TOML
        .parse()
        .expect("`lusterna-stubs/stubs.toml` is valid TOML");
    let Some(tables) = doc.get("stub").and_then(|r| r.as_array_of_tables()) else {
        return vec![];
    };
    tables
        .iter()
        .map(|t| Stub {
            krate: t
                .get("crate")
                .and_then(|c| c.as_str())
                .expect("every `[[stub]]` has a string `crate`")
                .to_owned(),
            lean: t.get("lean").and_then(|l| l.as_str()).map(str::to_owned),
        })
        .collect()
}

/// The Charon `--opaque` patterns for the items of the replaced crates that
/// have items in the extraction.
pub(super) fn opaque_patterns() -> impl Iterator<Item = String> {
    all()
        .into_iter()
        .filter(|r| r.lean.is_some())
        .map(|r| r.krate.replace('-', "_"))
}

/// The `cargo` arguments that patch each replaced crate with its stub crate.
pub(super) fn patch_args() -> Vec<String> {
    all()
        .iter()
        .flat_map(|r| {
            let stub = Path::new(LUSTERNA_STUBS).join(&r.krate);
            [
                "--config".to_owned(),
                format!("patch.crates-io.{}.path=\"{}\"", r.krate, stub.display()),
            ]
        })
        .collect()
}

/// The content of `Extraction/Lusterna.lean`: an import of the `lean` module
/// of each stub, and an instance of `Lusterna.OverflowSettings` with
/// the settings that the release profile of the workspace manifest
/// `manifest` gives to these crates. Fails if it gives them different
/// settings.
pub(super) fn lean_module(stubs: &[Stub], manifest: &Path) -> Result<String, StubsError> {
    let mut imports = String::new();
    let mut settings: Option<(&str, (bool, bool))> = None;
    for r in stubs {
        let Some(lean) = &r.lean else { continue };
        imports.push_str(&format!("public import {lean}\n"));
        let s = cargo::release_settings(manifest, &r.krate)?;
        match settings {
            Some((first, f)) if f != s => {
                return Err(StubsError::DifferentSettings {
                    first: first.to_owned(),
                    second: r.krate.clone(),
                });
            }
            Some(_) => {}
            None => settings = Some((&r.krate, s)),
        }
    }
    let instance = settings
        .map(|(_, (overflow_checks, debug_assertions))| {
            format!(
                "instance : Lusterna.OverflowSettings := {{ overflowChecks := {overflow_checks}, debugAssertions := {debug_assertions} }}\n"
            )
        })
        .unwrap_or_default();
    Ok(LUSTERNA_LEAN_TEMPLATE
        .replace("{imports}", &imports)
        .replace("{instance}", &instance))
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    #[test]
    fn lean_module_imports_models_and_declares_settings() {
        let stubs = [
            Stub {
                krate: "fixed".to_owned(),
                lean: Some("LusternaLib.Fixed".to_owned()),
            },
            Stub {
                krate: "fixed-macro-impl".to_owned(),
                lean: None,
            },
        ];
        let dir = tempfile::tempdir().unwrap();
        let manifest = dir.path().join("Cargo.toml");
        fs::write(
            &manifest,
            "[profile.release.package.fixed]\ndebug-assertions = true\n",
        )
        .unwrap();

        let module = lean_module(&stubs, &manifest).unwrap();

        assert!(module.contains("public import LusternaLib.Fixed\n"));
        assert!(module.contains(
            "instance : Lusterna.OverflowSettings := { overflowChecks := false, debugAssertions := true }\n"
        ));
        assert!(!module.contains("fixed-macro-impl"));
    }

    #[test]
    fn every_stub_has_a_crate() {
        for r in all() {
            assert!(
                Path::new(LUSTERNA_STUBS)
                    .join(&r.krate)
                    .join("Cargo.toml")
                    .is_file(),
                "no stub crate for {}",
                r.krate
            );
        }
    }
}
