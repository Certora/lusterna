module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the conversions of `U68F60` to the integer types
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem uscalar_val_ofNat {ty : UScalarTy} {i : Nat} (h : i < 2 ^ ty.numBits) :
    (⟨BitVec.ofNat ty.numBits i⟩ : UScalar ty).val = i := by
  simp only [UScalar.val, BitVec.toNat_ofNat]
  exact Nat.mod_eq_of_lt h

theorem iscalar_val_ofNat {ty : IScalarTy} {i : Nat} (h : i < 2 ^ (ty.numBits - 1)) :
    (⟨BitVec.ofNat ty.numBits i⟩ : IScalar ty).val = (i : Int) := by
  simp only [IScalar.val, BitVec.toInt_ofNat']
  have := ty.numBits_nonzero
  obtain ⟨k, hk⟩ : ∃ k, 2 ^ (ty.numBits - 1) = k := ⟨_, rfl⟩
  rw [hk] at h
  have h2 : (2 : Nat) ^ ty.numBits = 2 * k := by
    rw [← hk, ← Nat.pow_succ']
    congr 1
    omega
  apply Int.bmod_eq_of_le_mul_two <;> rw [h2] <;> push_cast <;> omega

theorem toUScalar_spec (cfg : OverflowSettings) (ty : UScalarTy) (a : U68F60)
    (h : a.bits.val / 2 ^ 60 < 2 ^ ty.numBits) :
    toUScalar cfg ty a ⦃ r => r.val = a.bits.val / 2 ^ 60 ⦄ := by
  unfold toUScalar
  dsimp only
  split
  · simp only [spec_ok]
    exact uscalar_val_ofNat h
  · rename_i hn
    exact absurd (Or.inl h) hn

theorem toUScalar_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)]
    (ty : UScalarTy) (a : U68F60) :
    toUScalar cfg ty a ⦃ r => a.bits.val / 2 ^ 60 < 2 ^ ty.numBits ∧
      r.val = a.bits.val / 2 ^ 60 ⦄fail := by
  apply fspec_of_spec_pre _ (toUScalar_spec cfg ty a)
  intro r hr
  unfold toUScalar at hr
  dsimp only at hr
  split at hr
  · rename_i hn
    simpa [hc.out, val, scale] using hn
  · simp at hr

theorem checkedToUScalar_spec (ty : UScalarTy) (a : U68F60) :
    match checkedToUScalar ty a with
    | some r => a.bits.val / 2 ^ 60 < 2 ^ ty.numBits ∧ r.val = a.bits.val / 2 ^ 60
    | none => 2 ^ ty.numBits ≤ a.bits.val / 2 ^ 60 := by
  unfold checkedToUScalar
  dsimp only
  by_cases h : val a / scale < 2 ^ ty.numBits
  · simp only [h, ↓reduceIte]
    exact ⟨h, uscalar_val_ofNat h⟩
  · simp only [h, ↓reduceIte]
    simp only [val, scale] at h
    omega

theorem val_saturatingToUScalar (ty : UScalarTy) (a : U68F60) :
    (saturatingToUScalar ty a).val = min (a.bits.val / 2 ^ 60) (2 ^ ty.numBits - 1) := by
  have : 0 < 2 ^ ty.numBits := Nat.two_pow_pos _
  exact uscalar_val_ofNat (by simp only [val, scale]; omega)

theorem toIScalar_spec (cfg : OverflowSettings) (ty : IScalarTy) (a : U68F60)
    (h : a.bits.val / 2 ^ 60 < 2 ^ (ty.numBits - 1)) :
    toIScalar cfg ty a ⦃ r => r.val = ((a.bits.val / 2 ^ 60 : Nat) : Int) ⦄ := by
  unfold toIScalar
  dsimp only
  split
  · simp only [spec_ok]
    exact iscalar_val_ofNat h
  · rename_i hn
    exact absurd (Or.inl h) hn

theorem toIScalar_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)]
    (ty : IScalarTy) (a : U68F60) :
    toIScalar cfg ty a ⦃ r => a.bits.val / 2 ^ 60 < 2 ^ (ty.numBits - 1) ∧
      r.val = ((a.bits.val / 2 ^ 60 : Nat) : Int) ⦄fail := by
  apply fspec_of_spec_pre _ (toIScalar_spec cfg ty a)
  intro r hr
  unfold toIScalar at hr
  dsimp only at hr
  split at hr
  · rename_i hn
    simpa [hc.out, val, scale] using hn
  · simp at hr

theorem checkedToIScalar_spec (ty : IScalarTy) (a : U68F60) :
    match checkedToIScalar ty a with
    | some r => a.bits.val / 2 ^ 60 < 2 ^ (ty.numBits - 1) ∧
        r.val = ((a.bits.val / 2 ^ 60 : Nat) : Int)
    | none => 2 ^ (ty.numBits - 1) ≤ a.bits.val / 2 ^ 60 := by
  unfold checkedToIScalar
  dsimp only
  by_cases h : val a / scale < 2 ^ (ty.numBits - 1)
  · simp only [h, ↓reduceIte]
    exact ⟨h, iscalar_val_ofNat h⟩
  · simp only [h, ↓reduceIte]
    simp only [val, scale] at h
    omega

theorem val_saturatingToIScalar (ty : IScalarTy) (a : U68F60) :
    (saturatingToIScalar ty a).val =
      ((min (a.bits.val / 2 ^ 60) (2 ^ (ty.numBits - 1) - 1) : Nat) : Int) := by
  have : 0 < 2 ^ (ty.numBits - 1) := Nat.two_pow_pos _
  exact iscalar_val_ofNat (by simp only [val, scale]; omega)

end Lusterna.Fixed

open Aeneas.Std.ScalarElab

uscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.from_fixed_spec
    [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) (h : a.bits.val / 2 ^ 60 ≤ «%S».max) :
    «%S».Insts.FixedTraitsFromFixed.from_fixed a ⦃ r => r.val = a.bits.val / 2 ^ 60 ⦄ :=
  Lusterna.Fixed.toUScalar_spec s _ a (by scalar_tac)

uscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.from_fixed_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a : fixed.types.U68F60) :
    «%S».Insts.FixedTraitsFromFixed.from_fixed a
    ⦃ r => a.bits.val / 2 ^ 60 ≤ «%S».max ∧ r.val = a.bits.val / 2 ^ 60 ⦄fail :=
  fspec_mono (Lusterna.Fixed.toUScalar_fspec s _ a) fun _ h => ⟨by scalar_tac, h.2⟩

uscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.checked_from_fixed_spec
    (a : fixed.types.U68F60) :
    «%S».Insts.FixedTraitsFromFixed.checked_from_fixed a ⦃ r => match r with
      | some x => a.bits.val / 2 ^ 60 ≤ «%S».max ∧ x.val = a.bits.val / 2 ^ 60
      | none => «%S».max < a.bits.val / 2 ^ 60 ⦄ := by
  simp only [«%S».Insts.FixedTraitsFromFixed.checked_from_fixed, spec_ok]
  have h := Lusterna.Fixed.checkedToUScalar_spec UScalarTy.«%S» a
  split at h
  · rename_i heq
    rw [heq]
    exact ⟨by scalar_tac, h.2⟩
  · rename_i heq
    rw [heq]
    scalar_tac

uscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.saturating_from_fixed_spec
    (a : fixed.types.U68F60) :
    «%S».Insts.FixedTraitsFromFixed.saturating_from_fixed a
    ⦃ r => r.val = min (a.bits.val / 2 ^ 60) «%S».max ⦄ := by
  simp only [«%S».Insts.FixedTraitsFromFixed.saturating_from_fixed, spec_ok,
    Lusterna.Fixed.val_saturatingToUScalar]
  scalar_tac

iscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.from_fixed_spec
    [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60)
    (h : ((a.bits.val / 2 ^ 60 : Nat) : Int) ≤ «%S».max) :
    «%S».Insts.FixedTraitsFromFixed.from_fixed a
    ⦃ r => r.val = ((a.bits.val / 2 ^ 60 : Nat) : Int) ⦄ :=
  Lusterna.Fixed.toIScalar_spec s _ a (by scalar_tac)

iscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.from_fixed_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a : fixed.types.U68F60) :
    «%S».Insts.FixedTraitsFromFixed.from_fixed a
    ⦃ r => ((a.bits.val / 2 ^ 60 : Nat) : Int) ≤ «%S».max ∧
      r.val = ((a.bits.val / 2 ^ 60 : Nat) : Int) ⦄fail :=
  fspec_mono (Lusterna.Fixed.toIScalar_fspec s _ a) fun _ h => ⟨by scalar_tac, h.2⟩

iscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.checked_from_fixed_spec
    (a : fixed.types.U68F60) :
    «%S».Insts.FixedTraitsFromFixed.checked_from_fixed a ⦃ r => match r with
      | some x => ((a.bits.val / 2 ^ 60 : Nat) : Int) ≤ «%S».max ∧
          x.val = ((a.bits.val / 2 ^ 60 : Nat) : Int)
      | none => «%S».max < ((a.bits.val / 2 ^ 60 : Nat) : Int) ⦄ := by
  simp only [«%S».Insts.FixedTraitsFromFixed.checked_from_fixed, spec_ok]
  have h := Lusterna.Fixed.checkedToIScalar_spec IScalarTy.«%S» a
  split at h
  · rename_i heq
    rw [heq]
    exact ⟨by scalar_tac, h.2⟩
  · rename_i heq
    rw [heq]
    scalar_tac

iscalar @[step] theorem «%S».Insts.FixedTraitsFromFixed.saturating_from_fixed_spec
    (a : fixed.types.U68F60) :
    «%S».Insts.FixedTraitsFromFixed.saturating_from_fixed a
    ⦃ r => r.val = min ((a.bits.val / 2 ^ 60 : Nat) : Int) «%S».max ⦄ := by
  simp only [«%S».Insts.FixedTraitsFromFixed.saturating_from_fixed, spec_ok,
    Lusterna.Fixed.val_saturatingToIScalar]
  scalar_tac
