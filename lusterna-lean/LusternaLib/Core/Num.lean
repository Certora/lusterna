module
public import Aeneas

/-!
# Integers

Models of the integer methods and conversions that the Aeneas library does not
have, in the style of `Aeneas/Std/Scalar/`:

* `TryFrom` between each pair of integer types that has no `From` impl
* `max_value` and `min_value`, which return `MAX` and `MIN`
* `saturating_mul`
-/

@[expose] public section

namespace Aeneas.Std

open Result ScalarElab

/-! ## `TryFrom` between integer types -/

/-- General fallible conversion from an unsigned to a signed scalar type. -/
def core.num.tryFromUScalarToIScalar {srcTy : UScalarTy} (tgtTy : IScalarTy)
  (i : UScalar srcTy) :
  Result (core.result.Result (IScalar tgtTy) core.num.error.TryFromIntError) :=
  if (i.val : Int) ≤ IScalar.max tgtTy then
    ok (.Ok (UScalar.hcast tgtTy i))
  else
    ok (.Err ())

/-- General fallible conversion from a signed to an unsigned scalar type. -/
def core.num.tryFromIScalarToUScalar {srcTy : IScalarTy} (tgtTy : UScalarTy)
  (i : IScalar srcTy) :
  Result (core.result.Result (UScalar tgtTy) core.num.error.TryFromIntError) :=
  if 0 ≤ i.val ∧ i.val ≤ UScalar.max tgtTy then
    ok (.Ok (IScalar.hcast tgtTy i))
  else
    ok (.Err ())

/-- General fallible conversion between signed scalar types. -/
def core.num.tryFromIScalar {srcTy : IScalarTy} (tgtTy : IScalarTy)
  (i : IScalar srcTy) :
  Result (core.result.Result (IScalar tgtTy) core.num.error.TryFromIntError) :=
  if IScalar.min tgtTy ≤ i.val ∧ i.val ≤ IScalar.max tgtTy then
    ok (.Ok (IScalar.cast tgtTy i))
  else
    ok (.Err ())

@[step]
theorem core.num.tryFromUScalar.spec {srcTy : UScalarTy} (tgtTy : UScalarTy)
    (i : UScalar srcTy) :
    core.num.tryFromUScalar tgtTy i
    ⦃ (r : core.result.Result (UScalar tgtTy) core.num.error.TryFromIntError) =>
      (i.val ≤ UScalar.max tgtTy → ∃ x, r = .Ok x ∧ x.val = i.val) ∧
      (¬ i.val ≤ UScalar.max tgtTy → r = .Err ()) ⦄ := by
  unfold core.num.tryFromUScalar
  split <;> rename_i hb <;> simp only [WP.spec_ok]
  · refine ⟨fun _ => ⟨_, rfl, ?_⟩, fun h => absurd hb h⟩
    have hcast := UScalar.cast_inBounds_spec tgtTy i hb
    simp only [lift, WP.spec_ok] at hcast
    exact hcast
  · exact ⟨fun h => absurd h hb, fun _ => trivial⟩

@[step]
theorem core.num.tryFromUScalarToIScalar.spec {srcTy : UScalarTy} (tgtTy : IScalarTy)
    (i : UScalar srcTy) :
    core.num.tryFromUScalarToIScalar tgtTy i
    ⦃ (r : core.result.Result (IScalar tgtTy) core.num.error.TryFromIntError) =>
      ((i.val : Int) ≤ IScalar.max tgtTy → ∃ x, r = .Ok x ∧ x.val = i.val) ∧
      (¬ (i.val : Int) ≤ IScalar.max tgtTy → r = .Err ()) ⦄ := by
  unfold core.num.tryFromUScalarToIScalar
  split <;> rename_i hb <;> simp only [WP.spec_ok]
  · refine ⟨fun _ => ⟨_, rfl, ?_⟩, fun h => absurd hb h⟩
    have hcast := UScalar.hcast_inBounds_spec tgtTy i hb
    simp only [lift, WP.spec_ok] at hcast
    exact hcast
  · exact ⟨fun h => absurd h hb, fun _ => trivial⟩

@[step]
theorem core.num.tryFromIScalarToUScalar.spec {srcTy : IScalarTy} (tgtTy : UScalarTy)
    (i : IScalar srcTy) :
    core.num.tryFromIScalarToUScalar tgtTy i
    ⦃ (r : core.result.Result (UScalar tgtTy) core.num.error.TryFromIntError) =>
      (0 ≤ i.val ∧ i.val ≤ UScalar.max tgtTy → ∃ x, r = .Ok x ∧ (x.val : Int) = i.val) ∧
      (¬ (0 ≤ i.val ∧ i.val ≤ UScalar.max tgtTy) → r = .Err ()) ⦄ := by
  unfold core.num.tryFromIScalarToUScalar
  split <;> rename_i hb <;> simp only [WP.spec_ok]
  · refine ⟨fun _ => ⟨_, rfl, ?_⟩, fun h => absurd hb h⟩
    have hcast := IScalar.hcast_inBounds_spec tgtTy i hb
    simp only [lift, WP.spec_ok] at hcast
    exact hcast
  · exact ⟨fun h => absurd h hb, fun _ => trivial⟩

@[step]
theorem core.num.tryFromIScalar.spec {srcTy : IScalarTy} (tgtTy : IScalarTy)
    (i : IScalar srcTy) :
    core.num.tryFromIScalar tgtTy i
    ⦃ (r : core.result.Result (IScalar tgtTy) core.num.error.TryFromIntError) =>
      (IScalar.min tgtTy ≤ i.val ∧ i.val ≤ IScalar.max tgtTy → ∃ x, r = .Ok x ∧ x.val = i.val) ∧
      (¬ (IScalar.min tgtTy ≤ i.val ∧ i.val ≤ IScalar.max tgtTy) → r = .Err ()) ⦄ := by
  unfold core.num.tryFromIScalar
  split <;> rename_i hb <;> simp only [WP.spec_ok]
  · refine ⟨fun _ => ⟨_, rfl, ?_⟩, fun h => absurd hb h⟩
    have hcast := IScalar.cast_inBounds_spec tgtTy i hb
    simp only [lift, WP.spec_ok] at hcast
    exact hcast
  · exact ⟨fun h => absurd h hb, fun _ => trivial⟩

/-- `TryFrom<src> for tgt` with the error `TryFromIntError`, for integer types
    `tgt` and `src` such as `U8` and `U16`. It unfolds to one of the general
    conversions above, whose step specs apply after `unfold`. -/
local macro "try_from_impl " tgt:ident src:ident : command => do
  let t := tgt.getId.toString
  let s := src.getId.toString
  let ptrSized := ["Usize", "Isize"]
  let module := if (ptrSized.contains t) != (ptrSized.contains s)
    then "core::convert::num::ptr_try_from_impls::" else "core::convert::num::"
  let path := module ++ "{core::convert::TryFrom<" ++ t.toLower ++ ", " ++ s.toLower ++
    ", core::num::error::TryFromIntError>}::try_from"
  let tgtSigned := t.startsWith "I"
  let srcSigned := s.startsWith "I"
  let helper := match srcSigned, tgtSigned with
    | false, false => `core.num.tryFromUScalar
    | false, true => `core.num.tryFromUScalarToIScalar
    | true, false => `core.num.tryFromIScalarToUScalar
    | true, true => `core.num.tryFromIScalar
  let tgtTy := Lean.Name.mkStr (if tgtSigned then `IScalarTy else `UScalarTy) t
  let declName := (t ++ ".Insts.CoreConvertTryFrom" ++ s ++ "TryFromIntError.try_from").toName
  let pathLit := Lean.Syntax.mkStrLit path
  `(@[expose, rust_fun $pathLit:str]
    def $(Lean.mkIdent declName) (i : $src) :
      Result (core.result.Result $tgt core.num.error.TryFromIntError) :=
      $(Lean.mkIdent helper) $(Lean.mkIdent tgtTy) i)

try_from_impl U8 U16
try_from_impl U8 U32
try_from_impl U16 U32
try_from_impl U8 U64
try_from_impl U16 U64
try_from_impl U32 U64
try_from_impl U8 U128
try_from_impl U16 U128
try_from_impl U32 U128
try_from_impl U64 U128
try_from_impl I8 I16
try_from_impl I8 I32
try_from_impl I16 I32
try_from_impl I8 I64
try_from_impl I16 I64
try_from_impl I32 I64
try_from_impl I8 I128
try_from_impl I16 I128
try_from_impl I32 I128
try_from_impl I64 I128
try_from_impl I8 U8
try_from_impl I8 U16
try_from_impl I16 U16
try_from_impl I8 U32
try_from_impl I16 U32
try_from_impl I32 U32
try_from_impl I8 U64
try_from_impl I16 U64
try_from_impl I32 U64
try_from_impl I64 U64
try_from_impl I8 U128
try_from_impl I16 U128
try_from_impl I32 U128
try_from_impl I64 U128
try_from_impl I128 U128
try_from_impl U8 I8
try_from_impl U16 I8
try_from_impl U32 I8
try_from_impl U64 I8
try_from_impl U128 I8
try_from_impl U8 I16
try_from_impl U16 I16
try_from_impl U32 I16
try_from_impl U64 I16
try_from_impl U128 I16
try_from_impl U8 I32
try_from_impl U16 I32
try_from_impl U32 I32
try_from_impl U64 I32
try_from_impl U128 I32
try_from_impl U8 I64
try_from_impl U16 I64
try_from_impl U32 I64
try_from_impl U64 I64
try_from_impl U128 I64
try_from_impl U8 I128
try_from_impl U16 I128
try_from_impl U32 I128
try_from_impl U64 I128
try_from_impl U128 I128
try_from_impl Isize Usize
try_from_impl Usize Isize
try_from_impl U8 Usize
try_from_impl U16 Usize
try_from_impl U64 Usize
try_from_impl U128 Usize
try_from_impl I8 Usize
try_from_impl I16 Usize
try_from_impl I32 Usize
try_from_impl I64 Usize
try_from_impl I128 Usize
try_from_impl U8 Isize
try_from_impl U16 Isize
try_from_impl U32 Isize
try_from_impl U64 Isize
try_from_impl U128 Isize
try_from_impl I8 Isize
try_from_impl I16 Isize
try_from_impl I32 Isize
try_from_impl I64 Isize
try_from_impl I128 Isize
try_from_impl Usize U32
try_from_impl Usize U64
try_from_impl Usize U128
try_from_impl Usize I8
try_from_impl Usize I16
try_from_impl Usize I32
try_from_impl Usize I64
try_from_impl Usize I128
try_from_impl Isize U16
try_from_impl Isize U32
try_from_impl Isize U64
try_from_impl Isize U128
try_from_impl Isize I32
try_from_impl Isize I64
try_from_impl Isize I128

/-! ## `max_value` and `min_value` -/

/- [core::num::{u8}::max_value] -/
uscalar def core.num.«%S».max_value : Result «%S» := ok core.num.«%S».MAX

/- [core::num::{u8}::min_value] -/
uscalar def core.num.«%S».min_value : Result «%S» := ok core.num.«%S».MIN

/- [core::num::{i8}::max_value] -/
iscalar def core.num.«%S».max_value : Result «%S» := ok core.num.«%S».MAX

/- [core::num::{i8}::min_value] -/
iscalar def core.num.«%S».min_value : Result «%S» := ok core.num.«%S».MIN

/-! ## Saturating multiplication -/

def UScalar.saturating_mul {ty : UScalarTy} (x y : UScalar ty) : UScalar ty :=
  ⟨ BitVec.ofNat _ (Min.min (UScalar.max ty) (x.val * y.val)) ⟩

/- [core::num::{u8}::saturating_mul] -/
uscalar def core.num.«%S».saturating_mul (x y : «%S») : Result «%S» :=
  ok (UScalar.saturating_mul x y)

def IScalar.saturating_mul {ty : IScalarTy} (x y : IScalar ty) : IScalar ty :=
  ⟨ BitVec.ofInt _ (Max.max (IScalar.min ty) (Min.min (IScalar.max ty) (x.val * y.val))) ⟩

/- [core::num::{i8}::saturating_mul] -/
iscalar def core.num.«%S».saturating_mul (x y : «%S») : Result «%S» :=
  ok (IScalar.saturating_mul x y)

end Aeneas.Std
