use std::path::Path;

use charon_lib::ullbc_ast::TranslatedCrate;

mod dyn_param_to_type_param;

use dyn_param_to_type_param::DynParamToTypeParam;

#[derive(Debug, thiserror::Error)]
pub enum RewriteError {}

pub trait RewriteRule {
    fn apply(tc: &TranslatedCrate) -> Result<Option<TranslatedCrate>, RewriteError>;
}

const RULES: &[fn(&TranslatedCrate) -> Result<Option<TranslatedCrate>, RewriteError>] =
    &[DynParamToTypeParam::apply];

/// If no rewrite rule was applied, return `Ok(None)`
pub fn run(llbc: &Path) -> anyhow::Result<Option<TranslatedCrate>> {
    let mut current = charon_lib::deserialize_llbc(llbc)?;
    let mut applied = false;
    let mut changed = true;
    while changed {
        changed = false;
        for rule in RULES {
            if let Some(next) = rule(&current)? {
                current = next;
                changed = true;
                applied = true;
            }
        }
    }
    Ok(applied.then_some(current))
}
