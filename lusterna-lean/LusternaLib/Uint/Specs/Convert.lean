module
public import LusternaLib.Uint.Specs.Basic

/-!
# `@[step]` theorems of the conversions and comparisons of the numbers of `uint`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec Lusterna.Uint

namespace Lusterna.Uint

theorem toUScalar_spec (ty : UScalarTy) {n : Usize} (a : Array U64 n)
    (h : val a < 2 ^ ty.numBits) :
    toUScalar ty a ⦃ r => r.val = val a ⦄ := by
  simp only [toUScalar, h, ↓reduceIte, spec_ok, UScalar.val, BitVec.toNat_ofNat]
  exact Nat.mod_eq_of_lt h

theorem toUScalar_fspec (ty : UScalarTy) {n : Usize} (a : Array U64 n) :
    toUScalar ty a ⦃ r => val a < 2 ^ ty.numBits ∧ r.val = val a ⦄fail := by
  apply fspec_of_spec_pre _ (toUScalar_spec ty a)
  intro r hr
  unfold toUScalar at hr
  split at hr
  · assumption
  · simp at hr

end Lusterna.Uint

@[step] theorem uint.from_u64_spec (N : Usize) (v : U64) (hN : 0 < N.val) :
    uint.from_u64 N v ⦃ r => val r = v.val ⦄ := by
  have : (2 : Nat) ^ 64 ≤ modulus N := Nat.pow_le_pow_right (by omega) (by omega)
  simp only [uint.from_u64, spec_ok]
  exact val_ofNat_of_lt (by scalar_tac)

@[step] theorem uint.from_u128_spec (N : Usize) (v : U128) (hN : 1 < N.val) :
    uint.from_u128 N v ⦃ r => val r = v.val ⦄ := by
  have : (2 : Nat) ^ 128 ≤ modulus N := Nat.pow_le_pow_right (by omega) (by omega)
  simp only [uint.from_u128, spec_ok]
  exact val_ofNat_of_lt (by scalar_tac)

@[step] theorem uint.low_u64_spec {N : Usize} (a : Array U64 N) :
    uint.low_u64 a ⦃ r => r.val = val a % 2 ^ 64 ⦄ := by
  simp only [uint.low_u64, spec_ok, UScalar.val, BitVec.toNat_ofNat, UScalarTy.numBits]

@[step] theorem uint.low_u128_spec {N : Usize} (a : Array U64 N) :
    uint.low_u128 a ⦃ r => r.val = val a % 2 ^ 128 ⦄ := by
  simp only [uint.low_u128, spec_ok, UScalar.val, BitVec.toNat_ofNat, UScalarTy.numBits]

@[step] theorem uint.as_u64_spec {N : Usize} (a : Array U64 N) (h : val a < 2 ^ 64) :
    uint.as_u64 a ⦃ r => r.val = val a ⦄ :=
  toUScalar_spec .U64 a h

@[step] theorem uint.as_u64_fspec {N : Usize} (a : Array U64 N) :
    uint.as_u64 a ⦃ r => val a < 2 ^ 64 ∧ r.val = val a ⦄fail :=
  toUScalar_fspec .U64 a

@[step] theorem uint.as_u128_spec {N : Usize} (a : Array U64 N) (h : val a < 2 ^ 128) :
    uint.as_u128 a ⦃ r => r.val = val a ⦄ :=
  toUScalar_spec .U128 a h

@[step] theorem uint.as_u128_fspec {N : Usize} (a : Array U64 N) :
    uint.as_u128 a ⦃ r => val a < 2 ^ 128 ∧ r.val = val a ⦄fail :=
  toUScalar_fspec .U128 a

@[step] theorem uint.try_into_u64_spec {N : Usize} (a : Array U64 N) :
    uint.try_into_u64 a ⦃ r => match r with
      | .Ok x => val a < 2 ^ 64 ∧ x.val = val a
      | .Err _ => 2 ^ 64 ≤ val a ⦄ := by
  unfold uint.try_into_u64
  split
  · rename_i h
    simp only [spec_ok, UScalar.val, BitVec.toNat_ofNat, UScalarTy.numBits]
    exact ⟨h, Nat.mod_eq_of_lt h⟩
  · rename_i h
    simp only [spec_ok]
    omega

@[step] theorem uint.try_into_u128_spec {N : Usize} (a : Array U64 N) :
    uint.try_into_u128 a ⦃ r => match r with
      | .Ok x => val a < 2 ^ 128 ∧ x.val = val a
      | .Err _ => 2 ^ 128 ≤ val a ⦄ := by
  unfold uint.try_into_u128
  split
  · rename_i h
    simp only [spec_ok, UScalar.val, BitVec.toNat_ofNat, UScalarTy.numBits]
    exact ⟨h, Nat.mod_eq_of_lt h⟩
  · rename_i h
    simp only [spec_ok]
    omega

@[step] theorem uint.eq_spec {N : Usize} (a b : Array U64 N) :
    uint.eq a b ⦃ r => r = decide (val a = val b) ⦄ := by
  simp only [uint.eq, spec_ok]

@[step] theorem uint.cmp_spec {N : Usize} (a b : Array U64 N) :
    uint.cmp a b ⦃ r => r = compare (val a) (val b) ⦄ := by
  simp only [uint.cmp, spec_ok]

@[step] theorem uint.is_zero_spec {N : Usize} (a : Array U64 N) :
    uint.is_zero a ⦃ r => r = decide (val a = 0) ⦄ := by
  simp only [uint.is_zero, spec_ok]
