use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

const DEFAULT_LOCKED: bool = true;

pub type PropertyId = usize;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash, JsonSchema)]
pub enum PropertyKind {
    Rule { fns: Vec<String> },
    Invariant { r#struct: String },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash, JsonSchema)]
pub struct Property {
    pub id: PropertyId,
    pub kind: PropertyKind,
    pub title: String,
    pub natspec: String,
    /// Whether the infer stage must return the property unchanged. Defaults
    /// to `DEFAULT_LOCKED`.
    #[serde(default = "default_locked")]
    pub locked: bool,
}

fn default_locked() -> bool {
    DEFAULT_LOCKED
}

#[derive(Debug, Default, Serialize, Deserialize, Clone, JsonSchema, PartialEq, Eq)]
pub struct Properties(pub Vec<Property>);

impl Properties {
    pub fn iter(&self) -> impl Iterator<Item = &Property> {
        self.0.iter()
    }

    pub fn locked(&self) -> impl Iterator<Item = &Property> {
        self.iter().filter(|p| p.locked)
    }
}

impl FromIterator<Property> for Properties {
    fn from_iter<I: IntoIterator<Item = Property>>(iter: I) -> Self {
        Self(iter.into_iter().collect())
    }
}
