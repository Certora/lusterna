use std::path::Path;

use charon_lib::ullbc_ast::TranslatedCrate;

mod dyn_param_to_type_param;

use dyn_param_to_type_param::DynParamToTypeParam;

#[derive(Debug, thiserror::Error)]
pub enum RewriteError {}

pub trait RewriteRule {
    fn apply(tc: &TranslatedCrate) -> Result<Option<TranslatedCrate>, RewriteError>;
}

/// If no rewrite rule was applied, return `Ok(None)`
pub fn run(llbc: &Path) -> anyhow::Result<Option<TranslatedCrate>> {
    let original_llbc = charon_lib::deserialize_llbc(llbc)?;

    let mut current = original_llbc.clone();
    let mut rewrite_appplied = false;

    let mut do_loop = true;
    while do_loop {
        do_loop = false;
        if let Some(res) = DynParamToTypeParam::apply(&current)? {
            current = res;
            do_loop = true;
            rewrite_appplied = true;
        }
    }

    if rewrite_appplied {
        Ok(Some(current))
    } else {
        Ok(None)
    }
}
