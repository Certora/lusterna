module
public import Aeneas

/-!
# `core::option::Option`

Models of the methods and trait impls of `Option` that the Aeneas library
does not have, in the style of `Aeneas/Std/Core/CoreOption.lean`.
-/

public section

namespace Aeneas.Std

open Result

/-- Pure model of `Option::unwrap_or_else`: the payload of `some`, and on
    `none` the result of `fnOnce`. -/
@[expose, rust_fun "core::option::{core::option::Option<@T>}::unwrap_or_else"]
def core.option.Option.unwrap_or_else
  {T F : Type _} (fnOnce : core.ops.function.FnOnce F Unit T)
  (x : Option T) (f : F) :
  Result T :=
  match x with
  | some value => ok value
  | none => fnOnce.call_once f ()

@[simp]
theorem core.option.Option.unwrap_or_else_some
  {T F : Type _} (fnOnce : core.ops.function.FnOnce F Unit T) (value : T) (f : F) :
  core.option.Option.unwrap_or_else fnOnce (some value) f = ok value := rfl

@[simp]
theorem core.option.Option.unwrap_or_else_none
  {T F : Type _} (fnOnce : core.ops.function.FnOnce F Unit T) (f : F) :
  core.option.Option.unwrap_or_else fnOnce none f = fnOnce.call_once f () := rfl

/-- Step spec for `Option::unwrap_or_else`: `post` is the postcondition of the
    closure, which `step` can infer with `+inferPost`. -/
@[step]
theorem core.option.Option.unwrap_or_else.spec {T F : Type _}
  (fnOnce : core.ops.function.FnOnce F Unit T) (x : Option T) (f : F) {post : T → Prop}
  (hf : x = none → fnOnce.call_once f () ⦃ post ⦄) :
  core.option.Option.unwrap_or_else fnOnce x f ⦃ y =>
    match x with
    | none => post y
    | some value => y = value ⦄ := by
  cases x with
  | none => exact hf rfl
  | some value => simp [core.option.Option.unwrap_or_else]

/-- Pure model of `Option::unwrap_or_default`: the payload of `some`, and on
    `none` the default value of `T`. -/
@[expose, rust_fun "core::option::{core::option::Option<@T>}::unwrap_or_default"]
def core.option.Option.unwrap_or_default
  {T : Type _} (defaultInst : core.default.Default T) (x : Option T) :
  Result T :=
  match x with
  | some value => ok value
  | none => defaultInst.default

@[simp]
theorem core.option.Option.unwrap_or_default_some
  {T : Type _} (defaultInst : core.default.Default T) (value : T) :
  core.option.Option.unwrap_or_default defaultInst (some value) = ok value := rfl

@[simp]
theorem core.option.Option.unwrap_or_default_none
  {T : Type _} (defaultInst : core.default.Default T) :
  core.option.Option.unwrap_or_default defaultInst none = defaultInst.default := rfl

/-- Step spec for `Option::unwrap_or_default`: `post` is the postcondition of
    `default`. -/
@[step]
theorem core.option.Option.unwrap_or_default.spec {T : Type _}
  (defaultInst : core.default.Default T) (x : Option T) {post : T → Prop}
  (hd : x = none → defaultInst.default ⦃ post ⦄) :
  core.option.Option.unwrap_or_default defaultInst x ⦃ y =>
    match x with
    | none => post y
    | some value => y = value ⦄ := by
  cases x with
  | none => exact hd rfl
  | some value => simp [core.option.Option.unwrap_or_default]

/-- Pure model of `Option::ok_or_else`: `Ok` of the payload of `some`, and on
    `none` `Err` of the result of `fnOnce`. -/
@[expose, rust_fun "core::option::{core::option::Option<@T>}::ok_or_else"]
def core.option.Option.ok_or_else
  {T E F : Type _} (fnOnce : core.ops.function.FnOnce F Unit E)
  (x : Option T) (f : F) :
  Result (core.result.Result T E) :=
  match x with
  | some value => ok (.Ok value)
  | none => do
      let e ← fnOnce.call_once f ()
      ok (.Err e)

@[simp]
theorem core.option.Option.ok_or_else_some
  {T E F : Type _} (fnOnce : core.ops.function.FnOnce F Unit E) (value : T) (f : F) :
  core.option.Option.ok_or_else fnOnce (some value) f = ok (.Ok value) := rfl

@[simp]
theorem core.option.Option.ok_or_else_none
  {T E F : Type _} (fnOnce : core.ops.function.FnOnce F Unit E) (f : F) :
  core.option.Option.ok_or_else fnOnce (none : Option T) f = (do
    let e ← fnOnce.call_once f ()
    ok (.Err e)) := rfl

/-- Step spec for `Option::ok_or_else`: `post` is the postcondition of the
    closure, which `step` can infer with `+inferPost`. -/
@[step]
theorem core.option.Option.ok_or_else.spec {T E F : Type _}
  (fnOnce : core.ops.function.FnOnce F Unit E) (x : Option T) (f : F) {post : E → Prop}
  (hf : x = none → fnOnce.call_once f () ⦃ post ⦄) :
  core.option.Option.ok_or_else fnOnce x f ⦃ y =>
    match x with
    | none => ∃ e, y = .Err e ∧ post e
    | some value => y = .Ok value ⦄ := by
  cases x with
  | none =>
    simp only [core.option.Option.ok_or_else]
    apply WP.spec_bind (hf rfl)
    intro e h
    simp [h]
  | some value => simp [core.option.Option.ok_or_else]

/-- Pure model of `Option::and_then`: `none` on `none`, and the result of
    `fnOnce` on the payload of `some`. -/
@[expose, rust_fun "core::option::{core::option::Option<@T>}::and_then"]
def core.option.Option.and_then
  {T U F : Type _} (fnOnce : core.ops.function.FnOnce F T (Option U))
  (x : Option T) (f : F) :
  Result (Option U) :=
  match x with
  | some value => fnOnce.call_once f value
  | none => ok none

@[simp]
theorem core.option.Option.and_then_some
  {T U F : Type _} (fnOnce : core.ops.function.FnOnce F T (Option U)) (value : T) (f : F) :
  core.option.Option.and_then fnOnce (some value) f = fnOnce.call_once f value := rfl

@[simp]
theorem core.option.Option.and_then_none
  {T U F : Type _} (fnOnce : core.ops.function.FnOnce F T (Option U)) (f : F) :
  core.option.Option.and_then fnOnce (none : Option T) f = ok (none : Option U) := rfl

/-- Step spec for `Option::and_then`: `post` is the postcondition of the
    closure, which `step` can infer with `+inferPost`. -/
@[step]
theorem core.option.Option.and_then.spec {T U F : Type _}
  (fnOnce : core.ops.function.FnOnce F T (Option U)) (x : Option T) (f : F)
  {post : T → Option U → Prop}
  (hf : ∀ value, x = some value → fnOnce.call_once f value ⦃ post value ⦄) :
  core.option.Option.and_then fnOnce x f ⦃ y =>
    match x with
    | none => y = none
    | some value => post value y ⦄ := by
  cases x with
  | none => simp [core.option.Option.and_then]
  | some value => exact hf value rfl

/-- Pure model of `Option::or_else`: `x` on `some`, and on `none` the result of
    `fnOnce`. -/
@[expose, rust_fun "core::option::{core::option::Option<@T>}::or_else"]
def core.option.Option.or_else
  {T F : Type _} (fnOnce : core.ops.function.FnOnce F Unit (Option T))
  (x : Option T) (f : F) :
  Result (Option T) :=
  match x with
  | some value => ok (some value)
  | none => fnOnce.call_once f ()

@[simp]
theorem core.option.Option.or_else_some
  {T F : Type _} (fnOnce : core.ops.function.FnOnce F Unit (Option T)) (value : T) (f : F) :
  core.option.Option.or_else fnOnce (some value) f = ok (some value) := rfl

@[simp]
theorem core.option.Option.or_else_none
  {T F : Type _} (fnOnce : core.ops.function.FnOnce F Unit (Option T)) (f : F) :
  core.option.Option.or_else fnOnce none f = fnOnce.call_once f () := rfl

/-- Step spec for `Option::or_else`: `post` is the postcondition of the
    closure, which `step` can infer with `+inferPost`. -/
@[step]
theorem core.option.Option.or_else.spec {T F : Type _}
  (fnOnce : core.ops.function.FnOnce F Unit (Option T)) (x : Option T) (f : F)
  {post : Option T → Prop}
  (hf : x = none → fnOnce.call_once f () ⦃ post ⦄) :
  core.option.Option.or_else fnOnce x f ⦃ y =>
    match x with
    | none => post y
    | some value => y = some value ⦄ := by
  cases x with
  | none => exact hf rfl
  | some value => simp [core.option.Option.or_else]

/-- `Option::branch` (`Try`): `some v ⇒ Continue v`, `none ⇒ Break none`. -/
@[expose, rust_fun
  "core::option::{core::ops::try_trait::Try<core::option::Option<@T>>}::branch"]
def core.option.Option.Insts.CoreOpsTry_traitTry.branch
  {T : Type _} :
  Option T → Std.Result (core.ops.control_flow.ControlFlow (Option Never) T)
  | some v => .ok (.Continue v)
  | none => .ok (.Break none)

@[step]
theorem core.option.Option.Insts.CoreOpsTry_traitTry.branch.step_spec
    {T : Type _} (x : Option T) :
    core.option.Option.Insts.CoreOpsTry_traitTry.branch x
    ⦃ (cf : core.ops.control_flow.ControlFlow (Option Never) T) =>
        match x with
        | some v => cf = .Continue v
        | none => cf = .Break none ⦄ := by
  cases x <;> simp [CoreOpsTry_traitTry.branch]

/-- `Option::from_residual` (`FromResidual`): converts the `none`-residual to
    `none`.

    Note that as `Never` is an empty type the `some` case is not possible. -/
@[expose, rust_fun
  "core::option::{core::ops::try_trait::FromResidual<core::option::Option<@T>, core::option::Option<!>>}::from_residual"]
def core.option.Option.Insts.CoreOpsTry_traitFromResidualOptionNever.from_residual
  (T : Type _) (r : Option Never) : Std.Result (Option T) :=
  match r with
  | some x => x.casesOn
  | none => .ok none

@[step]
theorem core.option.Option.Insts.CoreOpsTry_traitFromResidualOptionNever.from_residual.step_spec
    (T : Type _) :
    core.option.Option.Insts.CoreOpsTry_traitFromResidualOptionNever.from_residual T none
    ⦃ (out : Option T) => out = none ⦄ := by
  simp [from_residual]

end Aeneas.Std
