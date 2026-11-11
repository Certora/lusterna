use serde::{Deserialize, Serialize};

pub type PropertyId = usize;

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum PropertyKind {
    Rule { fns: Vec<String> },
    Invariant { r#struct: String },
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct Property {
    pub id: PropertyId,
    pub kind: PropertyKind,
    pub title: String,
    pub natspec: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Properties(pub Vec<Property>);
