//! Rewrites the `&dyn Trait` and `&mut dyn Trait` parameters of one function
//! `f` into `&T` and `&mut T`, with a new type parameter `T` and a new clause
//! `T: Trait` per parameter.
//!
//! The rule picks the first function `f` such that:
//! - `f` is not a trait method, and the crate refers to it only in direct
//!   calls;
//! - at each call of `f`, each `dyn` argument gets its value from an
//!   `unsize_cast` of a reference to a type that is not `dyn`, or, in a
//!   recursive call, from a `dyn` parameter of `f`;
//! - in `f`, each local that gets its value from a `dyn` parameter, through
//!   reborrows, moves and `unsize_cast`s from `dyn` to `dyn`, is used only as
//!   the receiver of a method call through the vtable or as a call argument.
//!
//! At each call of `f`, the cast is removed. The type argument for `T` is the
//! type that the cast takes a reference to, and the clause argument is the impl
//! whose vtable the cast uses.
//!
//! In `f`, a method call through the vtable becomes a call of the trait method
//! through the new clause. Where `f` passes a `&T` value to another function,
//! the rule casts it back to `&dyn Trait` with the vtable of the new clause.
//! That cast is a call argument of the kind above, so a later call of the rule
//! can rewrite the other function, and remove the cast.
//!
//! Soundness: the vtable of an impl holds the methods of that impl. A call
//! through that vtable calls the same function as a call of the trait method
//! through the impl. So at each call, the rewritten function computes the same
//! result as the original. A function with no caller in the crate is rewritten
//! into a function for every type that implements the trait. These include
//! every type that the `dyn` version can receive.

use std::collections::{HashMap, HashSet};

use charon_lib::llbc_ast::*;

use crate::{RewriteError, RewriteRule};

pub struct DynParamToTypeParam;

impl RewriteRule for DynParamToTypeParam {
    fn apply(tc: &TranslatedCrate) -> Result<Option<TranslatedCrate>, RewriteError> {
        // `target` is the function whose `dyn` parameters this call rewrites.
        // `bodies` maps `target` and each function that calls it to the `&dyn`
        // locals in its body that change type, each with its `Origin`.
        let Some((target, bodies)) = find_target(tc) else {
            return Ok(None);
        };
        let mut rewritten = tc.clone();
        let type_params = add_type_params(
            rewritten.fun_decls.get_mut(target.id).unwrap(),
            &target.params,
        );
        for (g_id, origins) in &bodies {
            let Body::Structured(body) = &mut rewritten.fun_decls.get_mut(*g_id).unwrap().body
            else {
                unreachable!()
            };
            // Where `target` passes its `dyn` value to another function, cast it back to `&dyn`.
            insert_casts_back(tc, body, &target, &type_params, origins);
            // Remove casts to `dyn`, turn vtable calls into trait calls, add type args to `target` calls.
            rewrite_statements(tc, body, &target, &type_params, origins);
            // Change the type of the rewritten locals from `&dyn Trait` to `&T` or the concrete type.
            retype_locals(body, &type_params, origins);
        }
        Ok(Some(rewritten))
    }
}

/// The function `f` and its `dyn` parameters.
struct Target {
    id: FunDeclId,
    params: Vec<DynParam>,
}

/// A parameter of type `&dyn Trait` or `&mut dyn Trait`.
struct DynParam {
    /// Position in the inputs of the signature.
    input: usize,
    pred: DynPredicate,
}

/// The type parameter `T` and a reference to the clause `T: Trait` that replace
/// a `dyn` parameter.
struct TypeParamWithClause {
    ty: Ty,
    clause: TraitRef,
}

/// Where a `&dyn` local of a body gets its value from.
#[derive(Clone, PartialEq)]
enum Origin {
    /// The `dyn` parameter of `f` with this index among the `dyn` parameters of
    /// `f`.
    Param(usize),
    /// An `unsize_cast` of a reference to `ty`, with the vtable of `impl_ref`.
    Cast { ty: Ty, impl_ref: PolyTraitRef },
}

/// The first function `f` that satisfies the conditions in the module doc. The
/// map holds `f` and each function that calls `f`, with the origin of each
/// local to rewrite in its body.
fn find_target(
    tc: &TranslatedCrate,
) -> Option<(Target, HashMap<FunDeclId, HashMap<LocalId, Origin>>)> {
    let only_called = only_called(tc);
    tc.fun_decls.iter_indexed().find_map(|(id, f)| {
        if !only_called.contains(&id) {
            return None;
        }
        let target = Target {
            id,
            params: dyn_params(f)?,
        };
        let bodies = tc
            .fun_decls
            .iter_indexed()
            .filter(|(g_id, g)| *g_id == id || calls_fun(g, id))
            .map(|(g_id, g)| Some((g_id, origins(tc, g, &target, g_id == id)?)))
            .collect::<Option<HashMap<_, _>>>()?;
        Some((target, bodies))
    })
}

/// The functions that the crate refers to only in direct calls. The rule cannot
/// change the signature of a function that the crate refers to otherwise, such
/// as through a function pointer.
fn only_called(tc: &TranslatedCrate) -> HashSet<FunDeclId> {
    let mut refs: HashMap<FunDeclId, usize> = HashMap::new();
    let mut count_ref = |id: &FunDeclId| *refs.entry(*id).or_default() += 1;
    tc.fun_decls
        .iter()
        .for_each(|x| x.dyn_visit(&mut count_ref));
    tc.global_decls
        .iter()
        .for_each(|x| x.dyn_visit(&mut count_ref));
    tc.type_decls
        .iter()
        .for_each(|x| x.dyn_visit(&mut count_ref));
    tc.trait_decls
        .iter()
        .for_each(|x| x.dyn_visit(&mut count_ref));
    tc.trait_impls
        .iter()
        .for_each(|x| x.dyn_visit(&mut count_ref));
    let mut calls: HashMap<FunDeclId, usize> = HashMap::new();
    for f in tc.fun_decls.iter() {
        f.dyn_visit(|call: &Call| {
            if let Some(id) = callee(call) {
                *calls.entry(id).or_default() += 1;
            }
        });
    }
    // The declaration itself holds one reference.
    refs.into_iter()
        .filter(|(id, n)| *n == calls.get(id).unwrap_or(&0) + 1)
        .map(|(id, _)| id)
        .collect()
}

/// Adds a type parameter and a clause to `f` per `dyn` parameter, and changes
/// the signature. Returns the new parameters in the order of `params`.
fn add_type_params(f: &mut FunDecl, params: &[DynParam]) -> Vec<TypeParamWithClause> {
    params
        .iter()
        .map(|param| {
            let var = f
                .generics
                .types
                .push_with(|index| TypeParam::new(index, format!("T{index}"), Variance::Unknown));
            let ty = TyKind::TypeVar(DeBruijnVar::Free(var)).into_ty();
            // The binder of `dyn Trait` binds one type with the clause `_dyn:
            // Trait`. Substitute the new type for `_dyn` to get the
            // clause `T: Trait`.
            let (pred_params, _) = param
                .pred
                .binder
                .clone()
                .apply_keep_params(&GenericArgs::new_types([ty.clone()].into()));
            let clause = pred_params.trait_clauses[TraitClauseId::ZERO].clone();
            let clause_id = f.generics.trait_clauses.push_with(|clause_id| TraitParam {
                clause_id,
                origin: PredicateOrigin::WhereClauseOnFn,
                ..clause.clone()
            });
            f.generics.types_outlive.extend(pred_params.types_outlive);
            let input = &mut f.signature.inputs[param.input];
            *input = with_pointee(input, ty.clone());
            TypeParamWithClause {
                ty,
                clause: TraitRef::new(
                    TraitRefKind::Clause(DeBruijnVar::Free(clause_id), RegionArgs::empty()),
                    clause.trait_.skip_binder,
                ),
            }
        })
        .collect()
}

/// Before each call that takes a local `x` with a `Param` origin as an argument
/// returned by `cast_back_args`, adds `tmp = unsize_cast<&dyn Trait>(x)` with
/// the vtable of the new clause, and passes `tmp` instead. Must run before
/// `retype_locals`, which removes the `dyn` type of `x`.
fn insert_casts_back(
    tc: &TranslatedCrate,
    body: &mut ExprBody,
    target: &Target,
    type_params: &[TypeParamWithClause],
    origins: &HashMap<LocalId, Origin>,
) {
    let locals = &mut body.locals;
    body.body.transform_sequences(|seq| {
        let span = seq[0].span;
        let StatementKind::Call { call, .. } = &mut seq[0].kind else {
            return vec![];
        };
        let mut casts = vec![];
        for arg in cast_back_args(call, target, origins) {
            let x = operand_local(&call.args[arg]).unwrap();
            let Origin::Param(i) = origins[&x] else {
                unreachable!()
            };
            let dyn_ty = locals.locals[x].ty.clone();
            let vtable = TyKind::Ref(
                Region::Static,
                TyKind::Adt(dyn_pointee(&dyn_ty).unwrap().vtable_ref(tc).unwrap()).into_ty(),
                RefKind::Shared,
            )
            .into_ty();
            let clause = PolyTraitRef(RegionBinder::empty(type_params[i].clause.clone()));
            let metadata = UnsizingMetadata::VTable(
                clause.clone(),
                ConstantExpr::new(ConstantExprKind::VTableRef(clause), vtable),
            );
            let tmp = locals.new_var(None, dyn_ty.clone());
            let x = std::mem::replace(&mut call.args[arg], Operand::Move(tmp.clone()));
            casts.push(Statement::new(
                span,
                StatementKind::StorageLive(tmp.as_local().unwrap()),
            ));
            casts.push(Statement::new(
                span,
                StatementKind::Assign(
                    tmp,
                    Rvalue::UnaryOp(UnOp::Cast(CastKind::Unsize(dyn_ty, metadata)), x),
                ),
            ));
        }
        casts
    });
}

/// Removes the pointer metadata from reborrows and the casts that define the
/// locals in `origins`, turns method calls through the vtable into calls
/// through the new clauses, and adds the new type and clause arguments to the
/// calls of `f`.
fn rewrite_statements(
    tc: &TranslatedCrate,
    body: &mut ExprBody,
    target: &Target,
    type_params: &[TypeParamWithClause],
    origins: &HashMap<LocalId, Origin>,
) {
    body.body
        .dyn_visit_in_body_mut(|st: &mut Statement| match &mut st.kind {
            // `&(*x) with_metadata(copy x.metadata)` becomes `&(*x)`, and `unsize_cast(y)` becomes
            // `y`.
            StatementKind::Assign(dst, rvalue)
                if dst.as_local().is_some_and(|d| origins.contains_key(&d)) =>
            {
                match rvalue {
                    Rvalue::Ref { ptr_metadata, .. } => *ptr_metadata = Operand::mk_const_unit(),
                    Rvalue::UnaryOp(UnOp::Cast(CastKind::Unsize(..)), op) => {
                        *rvalue = Rvalue::Use(op.clone(), WithRetag::No)
                    }
                    _ => {}
                }
            }
            StatementKind::Call { call, .. } => {
                if let Some((x, field, regions)) = vtable_call(call)
                    && let Some(Origin::Param(i)) = origins.get(&x)
                {
                    let method =
                        vtable_method(tc, &target.params[*i].pred, field, regions).unwrap();
                    call.func = FnOperand::Regular(FnPtr {
                        kind: Box::new(FnPtrKind::Trait(type_params[*i].clause.clone(), method)),
                        generics: Box::new(GenericArgs::new_lifetimes(regions.regions.clone())),
                    });
                } else if callee(call) == Some(target.id) {
                    let FnOperand::Regular(fn_ptr) = &mut call.func else {
                        unreachable!()
                    };
                    for param in &target.params {
                        let (ty, tref) = match &origins
                            [&operand_local(&call.args[param.input]).unwrap()]
                        {
                            Origin::Param(i) => (
                                type_params[*i].ty.clone(),
                                PolyTraitRef(RegionBinder::empty(type_params[*i].clause.clone())),
                            ),
                            Origin::Cast { ty, impl_ref } => (ty.clone(), impl_ref.clone()),
                        };
                        fn_ptr.generics.types.push_with(|_| ty);
                        fn_ptr.generics.trait_refs.push_with(|_| tref);
                    }
                }
            }
            _ => {}
        });
}

/// Replaces `dyn Trait` by the new type in the locals in `origins` and in the
/// places based on them, since each place records its type.
fn retype_locals(
    body: &mut ExprBody,
    type_params: &[TypeParamWithClause],
    origins: &HashMap<LocalId, Origin>,
) {
    let pointee = |origin: &Origin| match origin {
        Origin::Param(i) => type_params[*i].ty.clone(),
        Origin::Cast { ty, .. } => ty.clone(),
    };
    for (local, origin) in origins {
        let ty = &mut body.locals.locals[*local].ty;
        *ty = with_pointee(ty, pointee(origin));
    }
    body.body.dyn_visit_in_body_mut(|place: &mut Place| {
        if let Some(origin) = place.local_id().and_then(|l| origins.get(&l)) {
            place.ty = match place.ty.kind() {
                TyKind::DynTrait(_) => pointee(origin),
                TyKind::Ref(_, inner, _) if inner.kind().is_dyn_trait() => {
                    with_pointee(&place.ty, pointee(origin))
                }
                _ => place.ty.clone(),
            };
        }
    });
}

/// The `dyn` parameters of `f`, if `f` is a candidate for the rule.
fn dyn_params(f: &FunDecl) -> Option<Vec<DynParam>> {
    if !matches!(f.src, FunSource::Normal) || !matches!(f.body, Body::Structured(_)) {
        return None;
    }
    let mut params = vec![];
    for (input, ty) in f.signature.inputs.iter().enumerate() {
        match dyn_pointee(ty) {
            Some(pred) if supported(pred) => params.push(DynParam {
                input,
                pred: pred.clone(),
            }),
            _ if mentions_dyn(ty) => return None,
            _ => {}
        }
    }
    (!params.is_empty() && !mentions_dyn(&f.signature.output)).then_some(params)
}

/// Whether `pred` is a single trait with no associated type constraints and no
/// bound regions.
fn supported(pred: &DynPredicate) -> bool {
    let params = &pred.binder.params;
    params.regions.is_empty()
        && params.types.len() == 1
        && params.const_generics.is_empty()
        && params.trait_clauses.len() == 1
        && params.trait_clauses[TraitClauseId::ZERO]
            .trait_
            .regions
            .is_empty()
        && params.regions_outlive.is_empty()
        && params.trait_type_constraints.is_empty()
}

fn dyn_pointee(ty: &Ty) -> Option<&DynPredicate> {
    match ty.kind() {
        TyKind::Ref(_, inner, _) => inner.kind().as_dyn_trait(),
        _ => None,
    }
}

fn mentions_dyn(ty: &Ty) -> bool {
    let mut found = false;
    ty.dyn_visit(|t: &Ty| found |= t.kind().is_dyn_trait());
    found
}

/// `ty`, a reference, with its pointee replaced by `pointee`.
fn with_pointee(ty: &Ty, pointee: Ty) -> Ty {
    let TyKind::Ref(region, _, kind) = ty.kind() else {
        unreachable!()
    };
    TyKind::Ref(region.clone(), pointee, *kind).into_ty()
}

fn callee(call: &Call) -> Option<FunDeclId> {
    match &call.func {
        FnOperand::Regular(fn_ptr) => fn_ptr.kind.as_fun().copied(),
        FnOperand::Dynamic(..) => None,
    }
}

fn calls_fun(g: &FunDecl, f_id: FunDeclId) -> bool {
    let mut found = false;
    g.dyn_visit(|call: &Call| found |= callee(call) == Some(f_id));
    found
}

fn operand_local(op: &Operand) -> Option<LocalId> {
    match op {
        Operand::Move(place) | Operand::Copy(place) => place.as_local(),
        Operand::Const(_) => None,
    }
}

/// For a call `(copy (*x.metadata).field)(move x, ..)`, returns `x`, `field`
/// and the region arguments of the call.
fn vtable_call(call: &Call) -> Option<(LocalId, FieldId, &RegionArgs)> {
    let FnOperand::Dynamic(Operand::Copy(place), regions) = &call.func else {
        return None;
    };
    let (vtable, ProjectionElem::Field(None, field)) = place.as_projection()? else {
        return None;
    };
    let (metadata, ProjectionElem::Deref) = vtable.as_projection()? else {
        return None;
    };
    let (receiver, ProjectionElem::PtrMetadata) = metadata.as_projection()? else {
        return None;
    };
    let x = receiver.as_local()?;
    (operand_local(call.args.first()?)? == x).then_some((x, *field, regions))
}

/// The method of the trait of `pred` that the vtable holds in `field`, if
/// `regions` instantiates its generics.
fn vtable_method(
    tc: &TranslatedCrate,
    pred: &DynPredicate,
    field: FieldId,
    regions: &RegionArgs,
) -> Option<TraitMethodId> {
    let trait_ref = &pred.binder.params.trait_clauses[TraitClauseId::ZERO].trait_;
    let decl = tc.trait_decls.get(trait_ref.skip_binder.id)?;
    let vtable = tc.type_decls.get(decl.vtable.as_ref()?.id)?;
    let TypeDeclKind::Struct(fields) = &vtable.kind else {
        return None;
    };
    let name = fields.get(field)?.name.strip_prefix("method_")?;
    let (id, method) = decl
        .methods
        .iter_indexed()
        .find(|(_, method)| method.skip_binder.name.0.as_str() == name)?;
    let generics = &method.params;
    (generics.regions.len() == regions.regions.len()
        && generics.types.is_empty()
        && generics.const_generics.is_empty()
        && generics.trait_clauses.is_empty())
    .then_some(id)
}

/// The arguments of `call` that are locals with an origin `Param`, other than
/// the receiver of a method call through the vtable and the `dyn` arguments of
/// a recursive call of `f`.
fn cast_back_args(call: &Call, target: &Target, origins: &HashMap<LocalId, Origin>) -> Vec<usize> {
    let receiver = vtable_call(call).map(|(x, ..)| x);
    let recursive = callee(call) == Some(target.id);
    (0..call.args.len())
        .filter(|arg| {
            let Some(x) = operand_local(&call.args[*arg]) else {
                return false;
            };
            matches!(origins.get(&x), Some(Origin::Param(_)))
                && !(*arg == 0 && receiver == Some(x))
                && !(recursive && target.params.iter().any(|param| param.input == *arg))
        })
        .collect()
}

/// The origin that `rvalue` gives to the local it is assigned to. A `Param`
/// origin passes through reborrows, moves and `unsize_cast`s from `dyn` to
/// `dyn`. An `unsize_cast` of a reference to a type that is not `dyn` gives a
/// `Cast` origin.
fn assigned_origin(rvalue: &Rvalue, found: &HashMap<LocalId, Option<Origin>>) -> Option<Origin> {
    let param_origin = |src: LocalId| {
        found
            .get(&src)?
            .clone()
            .filter(|origin| matches!(origin, Origin::Param(_)))
    };
    match rvalue {
        Rvalue::Ref {
            place,
            ptr_metadata: Operand::Copy(metadata),
            ..
        } => {
            let (src, ProjectionElem::Deref) = place.as_projection()? else {
                return None;
            };
            let (metadata_src, ProjectionElem::PtrMetadata) = metadata.as_projection()? else {
                return None;
            };
            let src = src.as_local()?;
            (metadata_src.as_local()? == src).then(|| param_origin(src))?
        }
        Rvalue::Use(op, _) => param_origin(operand_local(op)?),
        Rvalue::UnaryOp(
            UnOp::Cast(CastKind::Unsize(_, UnsizingMetadata::VTableUpcast(path))),
            op,
        ) if path.is_empty() => param_origin(operand_local(op)?),
        Rvalue::UnaryOp(
            UnOp::Cast(CastKind::Unsize(target, UnsizingMetadata::VTable(impl_ref, _))),
            op,
        ) => {
            dyn_pointee(target)?;
            let (Operand::Move(src) | Operand::Copy(src)) = op else {
                return None;
            };
            src.as_local()?;
            let TyKind::Ref(_, ty, _) = src.ty.kind() else {
                return None;
            };
            Some(Origin::Cast {
                ty: ty.clone(),
                impl_ref: impl_ref.clone(),
            })
        }
        _ => None,
    }
}

/// The locals of `g` that the rule rewrites, with their origins, or `None` if
/// `g` breaks the conditions in the module doc. `own` is whether `g` is `f`.
/// The rewritten locals are the ones that get a `dyn` parameter of `f`, and the
/// `dyn` arguments of the calls of `f`.
fn origins(
    tc: &TranslatedCrate,
    g: &FunDecl,
    target: &Target,
    own: bool,
) -> Option<HashMap<LocalId, Origin>> {
    let Body::Structured(body) = &g.body else {
        return None;
    };
    let found = propagate_origins(body, target, own);

    let mut origins: HashMap<LocalId, Origin> = found
        .iter()
        .filter_map(|(local, origin)| match origin {
            Some(Origin::Param(i)) => Some((*local, Origin::Param(*i))),
            _ => None,
        })
        .collect();
    let mut args_ok = true;
    body.body.dyn_visit_in_body(|call: &Call| {
        if callee(call) != Some(target.id) {
            return;
        }
        for param in &target.params {
            match operand_local(&call.args[param.input]).and_then(|x| Some((x, found.get(&x)?))) {
                Some((x, Some(origin))) => {
                    origins.insert(x, origin.clone());
                }
                _ => args_ok = false,
            }
        }
    });
    (args_ok && only_rewritten_uses(tc, body, target, &found, &origins)).then_some(origins)
}

/// The origins of the `&dyn` locals of `body`, propagated through assignments.
/// `None` marks a local that gets different origins, for example in the two
/// branches of an `if`.
fn propagate_origins(
    body: &ExprBody,
    target: &Target,
    own: bool,
) -> HashMap<LocalId, Option<Origin>> {
    let mut found: HashMap<LocalId, Option<Origin>> = HashMap::new();
    if own {
        for (i, param) in target.params.iter().enumerate() {
            found.insert(LocalId::from_usize(param.input + 1), Some(Origin::Param(i)));
        }
    }
    loop {
        let before = found.clone();
        body.body.dyn_visit_in_body(|st: &Statement| {
            if let StatementKind::Assign(dst, rvalue) = &st.kind
                && let Some(dst) = dst.as_local()
                && dyn_pointee(&body.locals.locals[dst].ty).is_some()
                && let Some(origin) = assigned_origin(rvalue, &found)
            {
                match found.get(&dst) {
                    None => {
                        found.insert(dst, Some(origin));
                    }
                    Some(Some(existing)) if *existing != origin => {
                        found.insert(dst, None);
                    }
                    Some(_) => {}
                }
            }
        });
        if found == before {
            return found;
        }
    }
}

/// Whether each mention of a local in `origins` is in a statement that the rule
/// rewrites. Counts those mentions per local and compares with all mentions in
/// `body`. A local with more mentions has a use that the rule cannot rewrite,
/// such as a store into a struct field.
fn only_rewritten_uses(
    tc: &TranslatedCrate,
    body: &ExprBody,
    target: &Target,
    found: &HashMap<LocalId, Option<Origin>>,
    origins: &HashMap<LocalId, Origin>,
) -> bool {
    let mut allowed: HashMap<LocalId, usize> = HashMap::new();
    let mut allow = |local: LocalId, n: usize| *allowed.entry(local).or_default() += n;
    body.body
        .dyn_visit_in_body(|st: &Statement| match &st.kind {
            StatementKind::StorageLive(local) | StatementKind::StorageDead(local)
                if origins.contains_key(local) =>
            {
                allow(*local, 1)
            }
            StatementKind::Assign(dst, rvalue)
                if let Some(dst) = dst.as_local()
                    && let Some(origin) = origins.get(&dst)
                    && assigned_origin(rvalue, found).as_ref() == Some(origin) =>
            {
                allow(dst, 1);
                // `&(*x) with_metadata(copy x.metadata)` mentions `x` twice.
                match rvalue {
                    Rvalue::Ref { place, .. } => allow(place.local_id().unwrap(), 2),
                    Rvalue::Use(op, _) => allow(operand_local(op).unwrap(), 1),
                    Rvalue::UnaryOp(_, op)
                        if let Some(src) = operand_local(op)
                            && origins.contains_key(&src) =>
                    {
                        allow(src, 1)
                    }
                    _ => {}
                }
            }
            StatementKind::Call { call, .. } => {
                // The function operand and the receiver argument both mention
                // `x`.
                if let Some((x, field, regions)) = vtable_call(call)
                    && let Some(Origin::Param(i)) = origins.get(&x)
                    && vtable_method(tc, &target.params[*i].pred, field, regions).is_some()
                {
                    allow(x, 2);
                }
                if callee(call) == Some(target.id) {
                    for param in &target.params {
                        allow(operand_local(&call.args[param.input]).unwrap(), 1);
                    }
                }
                for arg in cast_back_args(call, target, origins) {
                    allow(operand_local(&call.args[arg]).unwrap(), 1);
                }
            }
            _ => {}
        });
    let mut mentions: HashMap<LocalId, usize> = HashMap::new();
    body.body
        .dyn_visit_in_body(|local: &LocalId| *mentions.entry(*local).or_default() += 1);
    origins
        .keys()
        .all(|local| allowed.get(local).unwrap_or(&0) == mentions.get(local).unwrap_or(&0))
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::*;

    /// The LLBC that Charon writes for `tests/fixtures/dyn_param_to_type_param/{name}.rs`.
    fn load(name: &str) -> TranslatedCrate {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("tests/fixtures/dyn_param_to_type_param/{name}.llbc"));
        charon_lib::deserialize_llbc(&path).unwrap()
    }

    fn fun_id(tc: &TranslatedCrate, name: &str) -> FunDeclId {
        tc.fun_decls
            .iter_indexed()
            .find(|(_, f)| {
                matches!(f.item_meta.name.name.last(), Some(PathElem::Ident(n, _)) if n == name)
            })
            .unwrap()
            .0
    }

    fn fun<'a>(tc: &'a TranslatedCrate, name: &str) -> &'a FunDecl {
        tc.fun_decls.get(fun_id(tc, name)).unwrap()
    }

    fn local(f: &FunDecl, name: &str) -> LocalId {
        let Body::Structured(body) = &f.body else {
            panic!()
        };
        body.locals
            .locals
            .iter()
            .find(|local| local.name.as_deref() == Some(name))
            .unwrap()
            .index
    }

    /// The number of method calls through a vtable in `f`.
    fn vtable_calls(f: &FunDecl) -> usize {
        let mut n = 0;
        f.dyn_visit(|call: &Call| n += matches!(call.func, FnOperand::Dynamic(..)) as usize);
        n
    }

    /// The number of `unsize_cast`s to `dyn` with the vtable of a trait impl or clause in `f`.
    fn casts_to_dyn(f: &FunDecl) -> usize {
        let mut n = 0;
        f.dyn_visit(|rvalue: &Rvalue| {
            n += matches!(
                rvalue,
                Rvalue::UnaryOp(
                    UnOp::Cast(CastKind::Unsize(_, UnsizingMetadata::VTable(..))),
                    _
                )
            ) as usize
        });
        n
    }

    #[test]
    fn only_called_excludes_function_used_as_pointer() {
        let tc = load("skip_fn_pointer");
        let only_called = only_called(&tc);
        assert!(!only_called.contains(&fun_id(&tc, "read")));
        assert!(only_called.contains(&fun_id(&tc, "through_pointer")));
    }

    #[test]
    fn dyn_params_lists_dyn_inputs() {
        let tc = load("rewrite_call_chain");
        let params = dyn_params(fun(&tc, "entry")).unwrap();
        assert_eq!(params.iter().map(|p| p.input).collect::<Vec<_>>(), [0, 1]);
        assert!(dyn_params(fun(&tc, "caller")).is_none());
    }

    #[test]
    fn dyn_params_skips_trait_impl_method() {
        let tc = load("skip_trait_method");
        assert!(dyn_params(fun(&tc, "read")).is_none());
    }

    #[test]
    fn find_target_picks_caller_before_callee() {
        // `helper` comes before `entry` in the crate, but `entry` passes its own `dyn` parameter to
        // `helper`, not a cast from a concrete type.
        let tc = load("rewrite_call_chain");
        let (target, bodies) = find_target(&tc).unwrap();
        let entry = fun_id(&tc, "entry");
        assert_eq!(target.id, entry);
        assert_eq!(
            bodies.keys().copied().collect::<HashSet<_>>(),
            HashSet::from([entry, fun_id(&tc, "caller")])
        );
    }

    #[test]
    fn origins_follow_reborrows_of_dyn_params() {
        let tc = load("rewrite_call_chain");
        let (target, bodies) = find_target(&tc).unwrap();
        let entry = fun(&tc, "entry");
        let origins = &bodies[&target.id];
        assert!(matches!(origins[&local(entry, "l")], Origin::Param(0)));
        assert!(matches!(origins[&local(entry, "m")], Origin::Param(1)));
        // `l` reaches `helper` through a reborrow and a cast from `dyn` to `dyn`. `m` reaches `put`
        // through a reborrow.
        let count = |i| {
            origins
                .values()
                .filter(|origin| matches!(origin, Origin::Param(j) if *j == i))
                .count()
        };
        assert_eq!(count(0), 3);
        assert_eq!(count(1), 2);
    }

    #[test]
    fn origins_of_caller_are_casts() {
        let tc = load("rewrite_call_chain");
        let (_, bodies) = find_target(&tc).unwrap();
        let origins = &bodies[&fun_id(&tc, "caller")];
        assert_eq!(origins.len(), 2);
        assert!(origins.values().all(|o| matches!(o, Origin::Cast { .. })));
    }

    #[test]
    fn apply_rewrites_one_function_per_call() {
        let tc = load("rewrite_call_chain");

        let first = DynParamToTypeParam::apply(&tc).unwrap().unwrap();
        let entry = fun(&first, "entry");
        assert!(dyn_params(entry).is_none());
        assert!(dyn_params(fun(&first, "helper")).is_some());
        assert_eq!(entry.generics.types.len(), 2);
        assert_eq!(vtable_calls(entry), 0);
        // `entry` passes `l` to `helper` through a new cast back to `&dyn Loader`, and `caller`
        // no longer casts its arguments.
        assert_eq!(casts_to_dyn(entry), 1);
        assert_eq!(casts_to_dyn(fun(&first, "caller")), 0);

        let second = DynParamToTypeParam::apply(&first).unwrap().unwrap();
        assert!(dyn_params(fun(&second, "helper")).is_none());
        assert_eq!(vtable_calls(fun(&second, "helper")), 0);
        assert_eq!(casts_to_dyn(fun(&second, "entry")), 0);

        assert!(DynParamToTypeParam::apply(&second).unwrap().is_none());
    }

    #[test]
    fn apply_rewrites_function_without_callers() {
        let tc = load("rewrite_entrypoint");
        let rewritten = DynParamToTypeParam::apply(&tc).unwrap().unwrap();
        let bump = fun(&rewritten, "bump");
        assert!(dyn_params(bump).is_none());
        assert_eq!(bump.generics.types.len(), 1);
        assert_eq!(bump.generics.trait_clauses.len(), 1);
        assert_eq!(vtable_calls(bump), 0);
    }

    #[test]
    fn skips_dyn_value_chosen_at_run_time() {
        assert!(find_target(&load("skip_runtime_choice")).is_none());
    }

    #[test]
    fn skips_dyn_value_from_struct_field() {
        assert!(find_target(&load("skip_struct_field")).is_none());
    }

    #[test]
    fn skips_dyn_param_stored_in_struct() {
        assert!(find_target(&load("skip_stored")).is_none());
    }

    #[test]
    fn skips_function_used_as_pointer() {
        assert!(find_target(&load("skip_fn_pointer")).is_none());
    }

    #[test]
    fn skips_trait_impl_method() {
        assert!(find_target(&load("skip_trait_method")).is_none());
    }
}
