module
public import LusternaLib.Uint.Specs.Basic

/-!
# `@[step]` theorems of the checked operations of the numbers of `uint`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Uint

@[step] theorem uint.checked_add_spec {N : Usize} (a b : Array U64 N) :
    uint.checked_add a b ⦃ r => match r with
      | some c => val a + val b < modulus N ∧ val c = val a + val b
      | none => modulus N ≤ val a + val b ⦄ := by
  simp only [uint.checked_add, checked, spec_ok]
  by_cases h : val a + val b < modulus N
  · simp only [h, ↓reduceIte]
    exact ⟨trivial, val_ofNat_of_lt h⟩
  · simp only [h, ↓reduceIte]
    omega

@[step] theorem uint.checked_sub_spec {N : Usize} (a b : Array U64 N) :
    uint.checked_sub a b ⦃ r => match r with
      | some c => val b ≤ val a ∧ val c = val a - val b
      | none => val a < val b ⦄ := by
  have := val_lt a
  simp only [uint.checked_sub, spec_ok]
  by_cases h : val b ≤ val a
  · simp only [h, ↓reduceIte]
    exact ⟨trivial, val_ofNat_of_lt (by omega)⟩
  · simp only [h, ↓reduceIte]
    omega

@[step] theorem uint.checked_mul_spec {N : Usize} (a b : Array U64 N) :
    uint.checked_mul a b ⦃ r => match r with
      | some c => val a * val b < modulus N ∧ val c = val a * val b
      | none => modulus N ≤ val a * val b ⦄ := by
  simp only [uint.checked_mul, checked, spec_ok]
  by_cases h : val a * val b < modulus N
  · simp only [h, ↓reduceIte]
    exact ⟨trivial, val_ofNat_of_lt h⟩
  · simp only [h, ↓reduceIte]
    omega

@[step] theorem uint.checked_div_spec {N : Usize} (a b : Array U64 N) :
    uint.checked_div a b ⦃ r => match r with
      | some c => val b ≠ 0 ∧ val c = val a / val b
      | none => val b = 0 ⦄ := by
  have := val_lt a
  have := Nat.div_le_self (val a) (val b)
  simp only [uint.checked_div, spec_ok]
  by_cases h : val b = 0
  · simp only [h, ↓reduceIte]
  · simp only [h, ↓reduceIte]
    exact ⟨h, val_ofNat_of_lt (by omega)⟩

@[step] theorem uint.checked_rem_spec {N : Usize} (a b : Array U64 N) :
    uint.checked_rem a b ⦃ r => match r with
      | some c => val b ≠ 0 ∧ val c = val a % val b
      | none => val b = 0 ⦄ := by
  have := val_lt a
  have := Nat.mod_le (val a) (val b)
  simp only [uint.checked_rem, spec_ok]
  by_cases h : val b = 0
  · simp only [h, ↓reduceIte]
  · simp only [h, ↓reduceIte]
    exact ⟨h, val_ofNat_of_lt (by omega)⟩
