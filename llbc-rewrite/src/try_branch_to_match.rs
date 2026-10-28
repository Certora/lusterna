//! Rewrites one `?` on a `Result`. Charon writes `?` as a call
//! `x = <Result<T, E> as Try>::branch(r)` and a match on the `ControlFlow` in
//! `x`. The rule gives `x` the type `Result<T, E>`:
//! - `x = branch(r)` becomes `x = r`;
//! - the match on `Continue` and `Break` becomes a match on `Ok` and `Err`;
//! - `(x as Continue)._0` becomes `(x as Ok)._0`;
//! - `y = (x as Break)._0` becomes `y = Err((x as Err)._0)`;
//! - the drops of `x` are removed.
//!
//! The rule picks the first `x`, in crate order, that is not the return value
//! or a parameter, and that has no other use.
//!
//! Soundness: `branch` is `match r { Ok(v) => Continue(v), Err(e) =>
//! Break(Err(e)) }`. It maps `Ok(v)` and `Err(e)` one to one onto the values of
//! `ControlFlow<Result<!, E>, T>`. The uses of `x` above read only its variant
//! and the payload of each variant, so they read the same through `r` as
//! through `branch(r)`. A drop of `x` runs after the match has moved or copied
//! the payload out of `x`, so it drops nothing.

use std::collections::HashSet;

use charon_lib::{llbc_ast::*, name_matcher::Pattern};

use crate::{RewriteError, RewriteRule};

/// `<Result<T, E> as Try>::branch`, as a pattern of Charon's name matcher.
const BRANCH: &str =
    "core::result::{impl core::ops::try_trait::Try for core::result::Result<_, _>}::branch";

pub struct TryBranchToMatch;

impl RewriteRule for TryBranchToMatch {
    fn apply(tc: &TranslatedCrate) -> Result<Option<TranslatedCrate>, RewriteError> {
        let Some((f_id, x)) = find_site(tc) else {
            return Ok(None);
        };
        let mut rewritten = tc.clone();
        let Body::Structured(body) = &mut rewritten.fun_decls.get_mut(f_id).unwrap().body else {
            unreachable!()
        };
        rewrite(tc, body, x);
        Ok(Some(rewritten))
    }
}

/// The functions that implement `<Result<T, E> as Try>::branch`.
fn branch_ids(tc: &TranslatedCrate) -> HashSet<FunDeclId> {
    let pattern = Pattern::parse(BRANCH).unwrap();
    tc.fun_decls
        .iter_indexed()
        .filter(|(_, f)| pattern.matches(tc, &f.item_meta.name))
        .map(|(id, _)| id)
        .collect()
}

fn is_branch(call: &Call, branch: &HashSet<FunDeclId>) -> bool {
    matches!(&call.func, FnOperand::Regular(fn_ptr) if fn_ptr.kind.as_fun().is_some_and(|id| branch.contains(id)))
}

/// The first function and local `x` that satisfy the conditions in the module
/// doc.
fn find_site(tc: &TranslatedCrate) -> Option<(FunDeclId, LocalId)> {
    let branch = branch_ids(tc);
    tc.fun_decls.iter_indexed().find_map(|(id, f)| {
        let Body::Structured(body) = &f.body else {
            return None;
        };
        let mut site = None;
        body.body.dyn_visit_in_body(|call: &Call| {
            if site.is_none()
                && is_branch(call, &branch)
                && let Some(x) = call.dest.as_local()
                && x.index() > body.locals.arg_count
                && only_match_uses(body, x, &branch)
            {
                site = Some(x);
            }
        });
        Some((id, site?))
    })
}

/// For a place `(x as v)._0`, returns `v`.
fn payload_variant(place: &Place, x: LocalId) -> Option<VariantId> {
    let (base, ProjectionElem::Field(Some(v), field)) = place.as_projection()? else {
        return None;
    };
    (base.as_local() == Some(x) && *field == FieldId::ZERO).then_some(*v)
}

/// Whether each mention of `x` in `body` is one of the uses in the module doc.
/// Counts those mentions and compares with all mentions of `x`.
fn only_match_uses(body: &ExprBody, x: LocalId, branch: &HashSet<FunDeclId>) -> bool {
    let mut allowed = 0;
    body.body.dyn_visit_in_body(|st: &Statement| {
        allowed += match &st.kind {
            StatementKind::StorageLive(local) | StatementKind::StorageDead(local) => *local == x,
            StatementKind::Call { call, .. } => {
                is_branch(call, branch) && call.dest.as_local() == Some(x)
            }
            StatementKind::Drop { place, .. }
            | StatementKind::Switch {
                data:
                    SwitchData {
                        scrutinee: SwitchScrutinee::Discriminant(place),
                        ..
                    },
                ..
            } => place.as_local() == Some(x),
            StatementKind::Assign(
                _,
                Rvalue::Use(Operand::Move(place) | Operand::Copy(place), _),
            ) => payload_variant(place, x).is_some(),
            _ => false,
        } as usize
    });
    let mut mentions = 0;
    body.body
        .dyn_visit_in_body(|local: &LocalId| mentions += (*local == x) as usize);
    allowed == mentions
}

/// The id of the variant `name` of the enum that `ty` names.
fn variant(tc: &TranslatedCrate, ty: &Ty, name: &str) -> Option<VariantId> {
    let TypeDeclKind::Enum(variants) = &tc.type_decls.get(ty.kind().as_adt()?.id)?.kind else {
        return None;
    };
    variants
        .iter()
        .find(|variant| variant.name == name)
        .map(|variant| variant.id)
}

/// Rewrites the uses of `x` in `body` as the module doc describes.
fn rewrite(tc: &TranslatedCrate, body: &mut ExprBody, x: LocalId) {
    let mut result_ty = None;
    body.body.dyn_visit_in_body(|call: &Call| {
        if call.dest.as_local() == Some(x) {
            result_ty = Some(call.args[0].ty().clone());
        }
    });
    let result_ty = result_ty.unwrap();
    let result_ref = result_ty.kind().as_adt().unwrap().clone();
    let err_ty = result_ref.generics.types[TypeVarId::new(1)].clone();
    let cont = variant(tc, &body.locals.locals[x].ty, "Continue").unwrap();
    let ok = variant(tc, &result_ty, "Ok").unwrap();
    let err = variant(tc, &result_ty, "Err").unwrap();
    let to_result = |v: VariantId| if v == cont { ok } else { err };

    body.body
        .dyn_visit_in_body_mut(|st: &mut Statement| match &mut st.kind {
            // `x = branch(r)` becomes `x = r`.
            StatementKind::Call { call, .. } if call.dest.as_local() == Some(x) => {
                st.kind = StatementKind::Assign(
                    call.dest.clone(),
                    Rvalue::Use(call.args[0].clone(), WithRetag::No),
                );
            }
            // `y = (x as Break)._0` becomes `y = Err((x as Break)._0)`. The projection becomes
            // `(x as Err)._0` below.
            StatementKind::Assign(dst, rvalue @ Rvalue::Use(..)) => {
                let Rvalue::Use(Operand::Move(place) | Operand::Copy(place), _) = rvalue else {
                    return;
                };
                if payload_variant(place, x).is_some_and(|v| v != cont) {
                    let Rvalue::Use(op, _) = rvalue.clone() else {
                        unreachable!()
                    };
                    *rvalue = Rvalue::Aggregate(
                        AggregateKind::Adt(
                            dst.ty.kind().as_adt().unwrap().clone(),
                            Some(err),
                            None,
                        ),
                        vec![op],
                    );
                }
            }
            StatementKind::Switch {
                data:
                    SwitchData {
                        scrutinee: SwitchScrutinee::Discriminant(place),
                        branches,
                        ..
                    },
                ..
            } if place.as_local() == Some(x) => {
                for (value, _) in branches {
                    let ConstantExprKind::Discriminant(_, v) = value.kind() else {
                        unreachable!()
                    };
                    *value = ConstantExpr::new(
                        ConstantExprKind::Discriminant(result_ref.clone(), to_result(*v)),
                        value.ty().clone(),
                    );
                }
            }
            _ => {}
        });

    // `x` gets the type `Result<T, E>`, and `(x as Continue)._0` and `(x as Break)._0` become
    // `(x as Ok)._0` and `(x as Err)._0`.
    body.locals.locals[x].ty = result_ty.clone();
    body.body.dyn_visit_in_body_mut(|place: &mut Place| {
        if place.as_local() == Some(x) {
            place.ty = result_ty.clone();
        } else if let PlaceKind::Projection(base, ProjectionElem::Field(Some(v), _)) =
            &mut place.kind
            && base.as_local() == Some(x)
        {
            *v = to_result(*v);
            if *v == err {
                place.ty = err_ty.clone();
            }
        }
    });

    body.body.visit_blocks_bwd(|block: &mut Block| {
        block.statements.retain(
            |st| !matches!(&st.kind, StatementKind::Drop { place, .. } if place.as_local() == Some(x)),
        )
    });
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::*;

    /// The LLBC that Charon writes for `tests/fixtures/try_branch_to_match/{name}.rs`.
    fn load(name: &str) -> TranslatedCrate {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("tests/fixtures/try_branch_to_match/{name}.llbc"));
        charon_lib::deserialize_llbc(&path).unwrap()
    }

    /// The number of calls of `branch` in the crate.
    fn branch_calls(tc: &TranslatedCrate) -> usize {
        let branch = branch_ids(tc);
        let mut n = 0;
        for f in tc.fun_decls.iter() {
            f.dyn_visit(|call: &Call| n += is_branch(call, &branch) as usize);
        }
        n
    }

    #[test]
    fn apply_rewrites_one_site_per_call() {
        let tc = load("rewrite_result");
        assert_eq!(branch_calls(&tc), 2);
        let first = TryBranchToMatch::apply(&tc).unwrap().unwrap();
        assert_eq!(branch_calls(&first), 1);
        let second = TryBranchToMatch::apply(&first).unwrap().unwrap();
        assert_eq!(branch_calls(&second), 0);
        assert!(TryBranchToMatch::apply(&second).unwrap().is_none());
    }

    #[test]
    fn apply_turns_control_flow_into_result() {
        let tc = load("rewrite_refmut");
        let (f_id, x) = find_site(&tc).unwrap();
        let rewritten = TryBranchToMatch::apply(&tc).unwrap().unwrap();
        let f = rewritten.fun_decls.get(f_id).unwrap();
        let Body::Structured(body) = &f.body else {
            panic!()
        };
        assert!(variant(&rewritten, &body.locals.locals[x].ty, "Ok").is_some());
        let mut drops = 0;
        f.dyn_visit(|st: &Statement| {
            drops += matches!(&st.kind, StatementKind::Drop { place, .. } if place.as_local() == Some(x))
                as usize
        });
        assert_eq!(drops, 0);
    }

    #[test]
    fn skips_control_flow_used_otherwise() {
        let tc = load("skip_control_flow_used");
        assert_eq!(branch_calls(&tc), 2);
        assert!(find_site(&tc).is_none());
    }
}
