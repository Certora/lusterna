use std::path::Path;

use charon_lib::{export::CrateData, options::SerializationFormat, ullbc_ast::TranslatedCrate};

mod dyn_param_to_type_param;
mod try_branch_to_match;

use dyn_param_to_type_param::DynParamToTypeParam;
use try_branch_to_match::TryBranchToMatch;

#[derive(Debug, thiserror::Error)]
pub enum RewriteError {}

pub trait RewriteRule {
    fn apply(tc: &TranslatedCrate) -> Result<Option<TranslatedCrate>, RewriteError>;
}

const RULES: &[fn(&TranslatedCrate) -> Result<Option<TranslatedCrate>, RewriteError>] =
    &[DynParamToTypeParam::apply, TryBranchToMatch::apply];

const MAX_ITERS: usize = 10_000;

/// If no rewrite rule was applied, return `Ok(None)`
pub fn run(llbc: &Path) -> anyhow::Result<Option<CrateData>> {
    let mut current = CrateData::deserialize_from_file(llbc, SerializationFormat::Json)?;
    let mut applied = false;
    let mut changed = true;
    let mut i = 0;
    while changed && i < MAX_ITERS {
        i += 1;
        changed = false;
        for rule in RULES {
            if let Some(next) = rule(&current.translated)? {
                current.translated = next;
                changed = true;
                applied = true;
            }
        }
    }
    Ok(applied.then_some(current))
}
