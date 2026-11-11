//! The spec, the input of a run: what to translate and which properties to
//! verify. It is read from `lusterna.json` in the workspace root, or from the
//! file given to `lusterna run`.

use serde::{Deserialize, Serialize};

use crate::stage::properties::Properties;

/// The input of a run: what to translate and which properties to verify.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    /// Charon `--start-from` patterns.
    #[serde(default)]
    pub entrypoints: Vec<String>,
    /// Charon `--opaque` patterns to add to the default patterns.
    #[serde(default)]
    pub add_opaque: Vec<String>,
    /// The properties to verify. With none and without `infer`, the run ends
    /// after `translate`.
    #[serde(default)]
    pub properties: Properties,
    /// Infer entrypoints and properties in addition to the given ones.
    #[serde(default)]
    pub infer: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_full_spec() {
        let spec: Spec = serde_json::from_str(
            r#"{
                "entrypoints": ["crate::vault"],
                "add_opaque": ["solana_program::sysvar::*"],
                "properties": [
                    {
                        "id": 1,
                        "kind": {"Invariant": {"struct": "vault::Vault"}},
                        "title": "Vault is solvent",
                        "natspec": "Shares never exceed assets."
                    }
                ],
                "infer": true
            }"#,
        )
        .unwrap();
        assert_eq!(spec.entrypoints, ["crate::vault"]);
        assert_eq!(spec.add_opaque, ["solana_program::sysvar::*"]);
        assert_eq!(spec.properties.0.len(), 1);
        assert!(spec.infer);
    }

    #[test]
    fn fields_default_to_empty() {
        let spec: Spec = serde_json::from_str("{}").unwrap();
        assert!(spec.entrypoints.is_empty());
        assert!(spec.add_opaque.is_empty());
        assert!(spec.properties.0.is_empty());
        assert!(!spec.infer);
    }

    #[test]
    fn rejects_unknown_field() {
        assert!(serde_json::from_str::<Spec>(r#"{"verify": false}"#).is_err());
    }
}
