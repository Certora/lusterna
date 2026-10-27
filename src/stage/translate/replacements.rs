use std::{
    fs, io,
    path::{Path, PathBuf},
};

/// The `lusterna-stubs` checkout.
const LUSTERNA_STUBS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/lusterna-stubs");

/// The Lean module `Extraction/Lusterna.lean` of a Solana crate, with
/// `{imports}` standing for the imports of the `lean` modules of the
/// replacements and `{instance}` for the instance of
/// `Lusterna.OverflowSettings`.
const LUSTERNA_LEAN_TEMPLATE: &str = include_str!("res/lusterna.lean.template");

#[derive(Debug, thiserror::Error)]
pub enum ReplacementsError {
    #[error("IO error: {0}")]
    Io(#[from] io::Error),
    #[error("cannot parse `{path}`: {source}")]
    Parse {
        path: PathBuf,
        #[source]
        source: toml_edit::TomlError,
    },
    #[error("`{path}`: every `[[replacement]]` needs a string `crate`")]
    MissingCrate { path: PathBuf },
    #[error(
        "the release profile gives `{first}` and `{second}` different overflow settings, but the Lean models take one `Lusterna.OverflowSettings`"
    )]
    DifferentSettings { first: String, second: String },
}

/// A crate that `lusterna-stubs` replaces in the Charon build of a Solana
/// crate, from `lusterna-stubs/replacements.toml`.
#[derive(Clone, Debug)]
pub struct Replacement {
    /// The name of the crate, and of its stub crate in `lusterna-stubs`.
    pub krate: String,
    /// The `LusternaLib` module with the Lean models of the items of the
    /// crate, if the crate has items in the extraction.
    pub lean: Option<String>,
}

impl Replacement {
    /// The Charon `--opaque` pattern for the items of the crate, if it has
    /// items in the extraction.
    pub fn opaque_pattern(&self) -> Option<String> {
        self.lean.as_ref().map(|_| self.krate.replace('-', "_"))
    }
}

/// Reads `lusterna-stubs/replacements.toml`.
pub fn load() -> Result<Vec<Replacement>, ReplacementsError> {
    let path = Path::new(LUSTERNA_STUBS).join("replacements.toml");
    let doc: toml_edit::DocumentMut =
        fs::read_to_string(&path)?
            .parse()
            .map_err(|source| ReplacementsError::Parse {
                path: path.clone(),
                source,
            })?;
    let Some(tables) = doc.get("replacement").and_then(|r| r.as_array_of_tables()) else {
        return Ok(vec![]);
    };
    tables
        .iter()
        .map(|t| {
            let krate = t
                .get("crate")
                .and_then(|c| c.as_str())
                .ok_or_else(|| ReplacementsError::MissingCrate { path: path.clone() })?;
            Ok(Replacement {
                krate: krate.to_owned(),
                lean: t.get("lean").and_then(|l| l.as_str()).map(str::to_owned),
            })
        })
        .collect()
}

/// The `cargo` arguments that patch each replaced crate with its stub crate.
pub fn patch_args(replacements: &[Replacement]) -> Vec<String> {
    replacements
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
pub fn lean_module(
    replacements: &[Replacement],
    manifest: &toml_edit::DocumentMut,
) -> Result<String, ReplacementsError> {
    let mut imports = String::new();
    let mut settings: Option<(&str, (bool, bool))> = None;
    for r in replacements {
        let Some(lean) = &r.lean else { continue };
        imports.push_str(&format!("public import {lean}\n"));
        let s = release_settings(manifest, &r.krate);
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

/// The `overflow-checks` and `debug-assertions` settings that the release
/// profile of `manifest` gives to the package `package`.
/// `[profile.release.package.<package>]` overrides
/// `[profile.release.package."*"]`, which overrides `[profile.release]`.
/// Both are off if no table sets them.
fn release_settings(manifest: &toml_edit::DocumentMut, package: &str) -> (bool, bool) {
    let release = manifest.get("profile").and_then(|p| p.get("release"));
    let packages = release.and_then(|r| r.get("package"));
    let tables = [
        release,
        packages.and_then(|p| p.get("*")),
        packages.and_then(|p| p.get(package)),
    ];
    let setting = |key: &str| {
        tables
            .iter()
            .flatten()
            .filter_map(|t| t.get(key).and_then(|v| v.as_bool()))
            .last()
            .unwrap_or(false)
    };
    (setting("overflow-checks"), setting("debug-assertions"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manifest(content: &str) -> toml_edit::DocumentMut {
        content.parse().unwrap()
    }

    #[test]
    fn release_settings_default_to_off() {
        assert_eq!(
            release_settings(&manifest("[workspace]\n"), "fixed"),
            (false, false)
        );
    }

    #[test]
    fn release_settings_take_the_most_specific_table() {
        let m = manifest(
            "[profile.release]\noverflow-checks = true\n\
             [profile.release.package.\"*\"]\ndebug-assertions = true\n\
             [profile.release.package.fixed]\noverflow-checks = false\n",
        );
        assert_eq!(release_settings(&m, "fixed"), (false, true));
        assert_eq!(release_settings(&m, "other"), (true, true));
    }

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
        let m = manifest("[profile.release.package.fixed]\ndebug-assertions = true\n");

        let module = lean_module(&replacements, &m).unwrap();

        assert!(module.contains("public import LusternaLib.Fixed\n"));
        assert!(module.contains(
            "instance : Lusterna.OverflowSettings := { overflowChecks := false, debugAssertions := true }\n"
        ));
        assert!(!module.contains("fixed-macro-impl"));
    }

    #[test]
    fn the_checkout_lists_valid_replacements() {
        let replacements = load().unwrap();
        for r in &replacements {
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
