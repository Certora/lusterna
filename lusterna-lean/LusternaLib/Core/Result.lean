module
public import Aeneas

/-!
# `core::result::Result`

Models of the methods of `Result` that the Aeneas library does not have, in
the style of `Aeneas/Std/Core/CoreResult.lean`.
-/

public section

namespace Aeneas.Std

open Result

/-- Pure model of `Result::is_err`: `true` iff the input is `Err`. -/
@[expose, rust_fun "core::result::{core::result::Result<@T, @E>}::is_err"]
def core.result.Result.is_err {T E : Type _} :
    core.result.Result T E → Std.Result Bool
  | .Ok _ => .ok false
  | .Err _ => .ok true

/-- Step spec for `Result::is_err`: returns `true` iff the input is `Err`. -/
@[step]
theorem core.result.Result.is_err.step_spec {T E : Type _}
    (r : core.result.Result T E) :
    core.result.Result.is_err r ⦃ (b : Bool) => b = !r.ok? ⦄ := by
  cases r <;> simp [is_err]

/-- Pure model of `Result::ok`: `some` of the payload of `Ok`, and `none` on
    `Err`. -/
@[expose, rust_fun "core::result::{core::result::Result<@T, @E>}::ok"]
def core.result.Result.ok {T E : Type _} :
    core.result.Result T E → Std.Result (Option T)
  | .Ok value => .ok (some value)
  | .Err _ => .ok none

@[simp]
theorem core.result.Result.ok_Ok {T E : Type _} (value : T) :
  core.result.Result.ok (.Ok value : core.result.Result T E) = .ok (some value) := rfl

@[simp]
theorem core.result.Result.ok_Err {T E : Type _} (error : E) :
  core.result.Result.ok (.Err error : core.result.Result T E) = .ok none := rfl

/-- Step spec for `Result::ok`. -/
@[step]
theorem core.result.Result.ok.step_spec {T E : Type _}
    (r : core.result.Result T E) :
    core.result.Result.ok r ⦃ (y : Option T) =>
      match r with
      | .Ok value => y = some value
      | .Err _ => y = none ⦄ := by
  cases r <;> simp

/-- Pure model of `Result::map`: maps the payload of `Ok` through `fnOnce`
    and leaves `Err` untouched. -/
@[expose, rust_fun "core::result::{core::result::Result<@T, @E>}::map"]
def core.result.Result.map
  {T E U F : Type _} (fnOnce : core.ops.function.FnOnce F T U)
  (x : core.result.Result T E) (f : F) :
  Std.Result (core.result.Result U E) :=
  match x with
  | .Ok value => do
      let mapped ← fnOnce.call_once f value
      .ok (.Ok mapped)
  | .Err error => .ok (.Err error)

@[simp]
theorem core.result.Result.map_Ok
  {T E U F : Type _} (fnOnce : core.ops.function.FnOnce F T U) (value : T) (f : F) :
  core.result.Result.map fnOnce (.Ok value : core.result.Result T E) f = (do
    let mapped ← fnOnce.call_once f value
    .ok (core.result.Result.Ok mapped : core.result.Result U E)) := rfl

@[simp]
theorem core.result.Result.map_Err
  {T E U F : Type _} (fnOnce : core.ops.function.FnOnce F T U) (error : E) (f : F) :
  core.result.Result.map fnOnce (.Err error : core.result.Result T E) f =
    .ok (core.result.Result.Err error : core.result.Result U E) := rfl

/-- Step spec for `Result::map`: `post` is the postcondition of the closure,
    which `step` can infer with `+inferPost`. -/
@[step]
theorem core.result.Result.map.spec {T E U F : Type _}
  (fnOnce : core.ops.function.FnOnce F T U) (x : core.result.Result T E) (f : F)
  {post : T → U → Prop}
  (hf : ∀ value, x = .Ok value → fnOnce.call_once f value ⦃ post value ⦄) :
  core.result.Result.map fnOnce x f ⦃ y =>
    match x with
    | .Ok value => ∃ mapped, y = .Ok mapped ∧ post value mapped
    | .Err error => y = .Err error ⦄ := by
  cases x with
  | Ok value =>
    simp only [core.result.Result.map]
    apply WP.spec_bind (hf value rfl)
    intro mapped h
    simp [h]
  | Err error => simp [core.result.Result.map]

end Aeneas.Std
