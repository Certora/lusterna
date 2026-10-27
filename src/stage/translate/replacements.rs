use std::path::Path;

use crate::utils::cargo::{self, CargoError};

/// The `lusterna-stubs` checkout.
const LUSTERNA_STUBS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/lusterna-stubs");

/// `lusterna-stubs/replacements.toml`.
const REPLACEMENTS_TOML: &str = include_str!("../../../lusterna-stubs/replacements.toml");

/// The Lean module `Extraction/Lusterna.lean` of a Solana crate, with
/// `{imports}` standing for the imports of the `lean` modules of the
/// replacements and `{instance}` for the instance of
/// `Lusterna.OverflowSettings`.
const LUSTERNA_LEAN_TEMPLATE: &str = include_str!("res/lusterna.lean.template");

#[derive(Debug, thiserror::Error)]
pub enum ReplacementsError {
    #[error(transparent)]
    Cargo(#[from] CargoError),
    #[error(
        "the release profile gives `{first}` and `{second}` different overflow settings, but the Lean models take one `Lusterna.OverflowSettings`"
    )]
    DifferentSettings { first: String, second: String },
}

/// A crate that `lusterna-stubs` replaces in the Charon build of a Solana
/// crate.
pub(super) struct Replacement {
    /// The name of the crate, and of its stub crate in `lusterna-stubs`.
    pub(super) krate: String,
    /// The `LusternaLib` module with the Lean models of the items of the
    /// crate, if the crate has items in the extraction.
    lean: Option<String>,
}

/// The replacements in `lusterna-stubs/replacements.toml`.
pub(super) fn all() -> Vec<Replacement> {
    let doc: toml_edit::DocumentMut = REPLACEMENTS_TOML
        .parse()
        .expect("`lusterna-stubs/replacements.toml` is valid TOML");
    let Some(tables) = doc.get("replacement").and_then(|r| r.as_array_of_tables()) else {
        return vec![];
    };
    tables
        .iter()
        .map(|t| Replacement {
            krate: t
                .get("crate")
                .and_then(|c| c.as_str())
                .expect("every `[[replacement]]` has a string `crate`")
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
/// of each replacement, and an instance of `Lusterna.OverflowSettings` with
/// the settings that the release profile of the workspace manifest
/// `manifest` gives to these crates. Fails if it gives them different
/// settings.
pub(super) fn lean_module(
    replacements: &[Replacement],
    manifest: &Path,
) -> Result<String, ReplacementsError> {
    let mut imports = String::new();
    let mut settings: Option<(&str, (bool, bool))> = None;
    for r in replacements {
        let Some(lean) = &r.lean else { continue };
        imports.push_str(&format!("public import {lean}\n"));
        let s = cargo::release_settings(manifest, &r.krate)?;
        match settings {
            Some((first, f)) if f != s => {
                return Err(ReplacementsError::DifferentSettings {
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
        let replacements = [
            Replacement {
                krate: "fixed".to_owned(),
                lean: Some("LusternaLib.Fixed".to_owned()),
            },
            Replacement {
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

        let module = lean_module(&replacements, &manifest).unwrap();

        assert!(module.contains("public import LusternaLib.Fixed\n"));
        assert!(module.contains(
            "instance : Lusterna.OverflowSettings := { overflowChecks := false, debugAssertions := true }\n"
        ));
        assert!(!module.contains("fixed-macro-impl"));
    }

    #[test]
    fn every_replacement_has_a_stub_crate() {
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
