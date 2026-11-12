module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the conversions of the integer types to `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem ofNat_spec (cfg : OverflowSettings) (n : Nat) (h : n < 2 ^ 68) :
    ofNat cfg n ⦃ r => r.bits.val = n * 2 ^ 60 ⦄ := by
  unfold ofNat
  split
  · simp only [spec_ok]
    exact bits_val_wrap_of_lt (show n * 2 ^ 60 < 2 ^ 128 by omega)
  · rename_i hn
    exact absurd (Or.inl h) hn

theorem ofNat_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)] (n : Nat) :
    ofNat cfg n ⦃ r => n < 2 ^ 68 ∧ r.bits.val = n * 2 ^ 60 ⦄fail := by
  apply fspec_of_spec_pre _ (ofNat_spec cfg n)
  intro r hr
  unfold ofNat at hr
  split at hr
  · rename_i hn
    simpa [hc.out] using hn
  · simp at hr

theorem checkedOfNat_spec (n : Nat) :
    match checkedOfNat n with
    | some c => n < 2 ^ 68 ∧ c.bits.val = n * 2 ^ 60
    | none => 2 ^ 68 ≤ n := by
  unfold checkedOfNat
  by_cases h : n < 2 ^ 68
  · simp only [h, ↓reduceIte]
    exact ⟨trivial, bits_val_wrap_of_lt (show n * 2 ^ 60 < 2 ^ 128 by omega)⟩
  · simp only [h, ↓reduceIte]
    omega

theorem ofInt_spec (cfg : OverflowSettings) (n : Int) (h : 0 ≤ n ∧ n < 2 ^ 68) :
    ofInt cfg n ⦃ r => r.bits.val = n.toNat * 2 ^ 60 ⦄ := by
  unfold ofInt
  split
  · simp only [spec_ok]
    exact bits_val_wrap_of_lt (show n.toNat * 2 ^ 60 < 2 ^ 128 by omega)
  · rename_i hn
    exact absurd h hn

theorem ofInt_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)] (n : Int) :
    ofInt cfg n ⦃ r => (0 ≤ n ∧ n < 2 ^ 68) ∧ r.bits.val = n.toNat * 2 ^ 60 ⦄fail := by
  apply fspec_of_spec_pre _ (ofInt_spec cfg n)
  intro r hr
  unfold ofInt at hr
  split at hr
  · assumption
  · simp [hc.out] at hr

theorem checkedOfInt_spec (n : Int) :
    match checkedOfInt n with
    | some c => (0 ≤ n ∧ n < 2 ^ 68) ∧ c.bits.val = n.toNat * 2 ^ 60
    | none => n < 0 ∨ 2 ^ 68 ≤ n := by
  unfold checkedOfInt
  by_cases h : 0 ≤ n ∧ n < 2 ^ 68
  · simp only [h, and_self, ↓reduceIte]
    exact ⟨trivial, bits_val_wrap_of_lt (show n.toNat * 2 ^ 60 < 2 ^ 128 by omega)⟩
  · simp only [h, ↓reduceIte]
    omega

end Lusterna.Fixed

open Aeneas.Std.ScalarElab

uscalar @[step] theorem «%S».Insts.FixedTraitsToFixed.to_fixed_spec [s : Lusterna.OverflowSettings]
    (n : «%S») (h : n.val < 2 ^ 68) :
    «%S».Insts.FixedTraitsToFixed.to_fixed n ⦃ r => r.bits.val = n.val * 2 ^ 60 ⦄ :=
  Lusterna.Fixed.ofNat_spec s n.val h

uscalar @[step] theorem «%S».Insts.FixedTraitsToFixed.to_fixed_fspec [s : Lusterna.OverflowSettings]
    [Fact (s.debugAssertions = true)] (n : «%S») :
    «%S».Insts.FixedTraitsToFixed.to_fixed n
    ⦃ r => n.val < 2 ^ 68 ∧ r.bits.val = n.val * 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.ofNat_fspec s n.val

uscalar @[step] theorem «%S».Insts.FixedTraitsToFixed.checked_to_fixed_spec (n : «%S») :
    «%S».Insts.FixedTraitsToFixed.checked_to_fixed n ⦃ r => match r with
      | some c => n.val < 2 ^ 68 ∧ c.bits.val = n.val * 2 ^ 60
      | none => 2 ^ 68 ≤ n.val ⦄ := by
  simp only [«%S».Insts.FixedTraitsToFixed.checked_to_fixed, spec_ok]
  exact Lusterna.Fixed.checkedOfNat_spec n.val

iscalar @[step] theorem «%S».Insts.FixedTraitsToFixed.to_fixed_spec [s : Lusterna.OverflowSettings]
    (n : «%S») (h : 0 ≤ n.val ∧ n.val < 2 ^ 68) :
    «%S».Insts.FixedTraitsToFixed.to_fixed n ⦃ r => r.bits.val = n.val.toNat * 2 ^ 60 ⦄ :=
  Lusterna.Fixed.ofInt_spec s n.val h

iscalar @[step] theorem «%S».Insts.FixedTraitsToFixed.to_fixed_fspec [s : Lusterna.OverflowSettings]
    [Fact (s.debugAssertions = true)] (n : «%S») :
    «%S».Insts.FixedTraitsToFixed.to_fixed n
    ⦃ r => (0 ≤ n.val ∧ n.val < 2 ^ 68) ∧ r.bits.val = n.val.toNat * 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.ofInt_fspec s n.val

iscalar @[step] theorem «%S».Insts.FixedTraitsToFixed.checked_to_fixed_spec (n : «%S») :
    «%S».Insts.FixedTraitsToFixed.checked_to_fixed n ⦃ r => match r with
      | some c => (0 ≤ n.val ∧ n.val < 2 ^ 68) ∧ c.bits.val = n.val.toNat * 2 ^ 60
      | none => n.val < 0 ∨ 2 ^ 68 ≤ n.val ⦄ := by
  simp only [«%S».Insts.FixedTraitsToFixed.checked_to_fixed, spec_ok]
  exact Lusterna.Fixed.checkedOfInt_spec n.val
