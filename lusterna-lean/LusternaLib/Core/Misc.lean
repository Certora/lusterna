module
public import Aeneas

/-!
# Other items of `core`

Models of `core` items that the Aeneas library does not have, in the style of
`Aeneas/Std/Core/`:

* `mem::drop` and `hint::must_use`
* `bool::then_some`
* `PartialEq<&B> for &mut A`, method `ne`
* `RangeInclusive::{start, end, contains}`
-/

public section

namespace Aeneas.Std

open Result

/-- `core::mem::drop`: takes ownership of the value and returns nothing. -/
@[expose, rust_fun "core::mem::drop"]
def core.mem.drop {T : Type _} (_x : T) : Result Unit := ok ()

@[simp]
theorem core.mem.drop_eq {T : Type _} (x : T) : core.mem.drop x = ok () := rfl

/-- `core::hint::must_use`: returns its argument. -/
@[expose, rust_fun "core::hint::must_use"]
def core.hint.must_use {T : Type _} (x : T) : Result T := ok x

@[simp]
theorem core.hint.must_use_eq {T : Type _} (x : T) : core.hint.must_use x = ok x := rfl

/-- Pure model of `bool::then_some`: `some t` on `true`, and `none` on
    `false`. -/
@[expose, rust_fun "core::bool::{bool}::then_some"]
def core.bool.Bool.then_some {T : Type _} (b : Bool) (t : T) : Result (Option T) :=
  if b then ok (some t) else ok none

@[simp]
theorem core.bool.Bool.then_some_true {T : Type _} (t : T) :
  core.bool.Bool.then_some true t = ok (some t) := rfl

@[simp]
theorem core.bool.Bool.then_some_false {T : Type _} (t : T) :
  core.bool.Bool.then_some false t = ok none := rfl

@[step]
theorem core.bool.Bool.then_some.spec {T : Type _} (b : Bool) (t : T) :
  core.bool.Bool.then_some b t ⦃ y => y = if b then some t else none ⦄ := by
  cases b <;> simp

/-- `PartialEq<&B> for &mut A`, method `ne`: `ne` of `PartialEq<B> for A` on
    the referenced values. -/
@[expose, rust_fun "core::cmp::impls::{core::cmp::PartialEq<&'1 mut @A, &'0 @B>}::ne"]
def Mut1A.Insts.CoreCmpPartialEqShared0B.ne
  {A B : Type _} (partialEqInst : core.cmp.PartialEq A B) (a : A) (b : B) : Result Bool :=
  partialEqInst.ne a b

@[simp]
theorem Mut1A.Insts.CoreCmpPartialEqShared0B.ne_eq
  {A B : Type _} (partialEqInst : core.cmp.PartialEq A B) (a : A) (b : B) :
  Mut1A.Insts.CoreCmpPartialEqShared0B.ne partialEqInst a b = partialEqInst.ne a b := rfl

/-- Pure model of `RangeInclusive::contains`: `start <= item && item <= end`.
    After the range is exhausted, the upper bound is exclusive:
    `start <= item && item < end`. As in Rust, the comparison with the upper
    bound only runs if the comparison with the lower bound is `true`. -/
@[expose, rust_fun "core::ops::range::{core::ops::range::RangeInclusive<@Idx>}::contains"]
def core.ops.range.RangeInclusive.contains {Idx U : Type _}
  (_partialOrdIdxInst : core.cmp.PartialOrd Idx Idx)
  (partialOrdIdxUInst : core.cmp.PartialOrd Idx U)
  (partialOrdUIdxInst : core.cmp.PartialOrd U Idx)
  (r : core.ops.range.RangeInclusive Idx) (item : U) : Result Bool := do
  let lower ← partialOrdIdxUInst.le r.start item
  if lower then
    if r.exhausted then partialOrdUIdxInst.lt item r.«end»
    else partialOrdUIdxInst.le item r.«end»
  else ok false

end Aeneas.Std

/-!
`RangeInclusive::start` and `end` are outside `namespace Aeneas.Std`, because
there `core.ops.range.RangeInclusive.start` and `«end»` are the fields of the
structure.
-/

open Aeneas.Std Result

/-- `RangeInclusive::start`: the lower bound, also after the range is
    exhausted. -/
@[expose, rust_fun "core::ops::range::{core::ops::range::RangeInclusive<@Idx>}::start"]
def core.ops.range.RangeInclusive.start {Idx : Type _}
  (r : core.ops.range.RangeInclusive Idx) : Result Idx :=
  ok r.start

/-- `RangeInclusive::end`: the upper bound, also after the range is
    exhausted. -/
@[expose, rust_fun "core::ops::range::{core::ops::range::RangeInclusive<@Idx>}::end"]
def core.ops.range.RangeInclusive.«end» {Idx : Type _}
  (r : core.ops.range.RangeInclusive Idx) : Result Idx :=
  ok r.«end»
