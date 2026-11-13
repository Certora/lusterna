module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the checked and saturating operations of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP

namespace Lusterna.Fixed

theorem checkedAdd_spec (a b : U68F60) :
    match checkedAdd a b with
    | some c => a.bits.val + b.bits.val < 2 ^ 128 ∧ c.bits.val = a.bits.val + b.bits.val
    | none => 2 ^ 128 ≤ a.bits.val + b.bits.val := by
  unfold checkedAdd checked
  by_cases h : val a + val b < 2 ^ 128
  · simp only [h, ite_true];  simp only [val] at h;
    exact ⟨h, bits_val_wrap_of_lt h⟩
  · simp only [val] at h ⊢; grind

theorem checkedSub_spec (a b : U68F60) :
    match checkedSub a b with
    | some c => b.bits.val ≤ a.bits.val ∧ c.bits.val = a.bits.val - b.bits.val
    | none => a.bits.val < b.bits.val := by
  have := bits_val_lt a
  unfold checkedSub
  by_cases h : val b ≤ val a
  · simp only [h]
    exact ⟨h, bits_val_wrap_of_lt (by simp only [val] at h ⊢; omega)⟩
  · simp only [val] at ⊢ h; grind

theorem checkedMul_spec (a b : U68F60) :
    match checkedMul a b with
    | some c => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
        c.bits.val = a.bits.val * b.bits.val / 2 ^ 60
    | none => 2 ^ 128 ≤ a.bits.val * b.bits.val / 2 ^ 60 := by
  unfold checkedMul checked
  by_cases h : val a * val b / scale < 2 ^ 128
  · simp only [h]
    exact ⟨h, bits_val_wrap_of_lt h⟩
  · simp only [val] at ⊢ h; grind

theorem checkedDiv_spec (a b : U68F60) :
    match checkedDiv a b with
    | some c => b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128 ∧
        c.bits.val = a.bits.val * 2 ^ 60 / b.bits.val
    | none => b.bits.val = 0 ∨ 2 ^ 128 ≤ a.bits.val * 2 ^ 60 / b.bits.val := by
  unfold checkedDiv checked
  by_cases h0 : val b = 0
  · simp only [h0]
    exact Or.inl h0
  · by_cases h : val a * scale / val b < 2 ^ 128
    · simp only [h0, h]
      exact ⟨h0, h, bits_val_wrap_of_lt h⟩
    · simp only [h0, h]
      simp only [val, scale] at h
      grind

theorem bits_val_saturatingAdd (a b : U68F60) :
    (saturatingAdd a b).bits.val = min (a.bits.val + b.bits.val) (2 ^ 128 - 1) :=
  bits_val_wrap_of_lt (by simp only [val]; omega)

theorem bits_val_saturatingSub (a b : U68F60) :
    (saturatingSub a b).bits.val = a.bits.val - b.bits.val := by
  have := bits_val_lt a
  exact bits_val_wrap_of_lt (by simp only [val]; omega)

theorem bits_val_saturatingMul (a b : U68F60) :
    (saturatingMul a b).bits.val = min (a.bits.val * b.bits.val / 2 ^ 60) (2 ^ 128 - 1) :=
  bits_val_wrap_of_lt (by simp only [val, scale]; omega)

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.checked_add_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.checked_add a b ⦃ r => match r with
      | some c => a.bits.val + b.bits.val < 2 ^ 128 ∧ c.bits.val = a.bits.val + b.bits.val
      | none => 2 ^ 128 ≤ a.bits.val + b.bits.val ⦄ := by
  simp only [fixed.types.U68F60.checked_add, spec_ok]
  exact Lusterna.Fixed.checkedAdd_spec a b

@[step] theorem fixed.types.U68F60.checked_sub_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.checked_sub a b ⦃ r => match r with
      | some c => b.bits.val ≤ a.bits.val ∧ c.bits.val = a.bits.val - b.bits.val
      | none => a.bits.val < b.bits.val ⦄ := by
  simp only [fixed.types.U68F60.checked_sub, spec_ok]
  exact Lusterna.Fixed.checkedSub_spec a b

@[step] theorem fixed.types.U68F60.checked_mul_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.checked_mul a b ⦃ r => match r with
      | some c => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
          c.bits.val = a.bits.val * b.bits.val / 2 ^ 60
      | none => 2 ^ 128 ≤ a.bits.val * b.bits.val / 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.checked_mul, spec_ok]
  exact Lusterna.Fixed.checkedMul_spec a b

@[step] theorem fixed.types.U68F60.checked_div_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.checked_div a b ⦃ r => match r with
      | some c => b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128 ∧
          c.bits.val = a.bits.val * 2 ^ 60 / b.bits.val
      | none => b.bits.val = 0 ∨ 2 ^ 128 ≤ a.bits.val * 2 ^ 60 / b.bits.val ⦄ := by
  simp only [fixed.types.U68F60.checked_div, spec_ok]
  exact Lusterna.Fixed.checkedDiv_spec a b

@[step] theorem fixed.types.U68F60.saturating_add_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.saturating_add a b
    ⦃ r => r.bits.val = min (a.bits.val + b.bits.val) (2 ^ 128 - 1) ⦄ := by
  simp only [fixed.types.U68F60.saturating_add, spec_ok, Lusterna.Fixed.bits_val_saturatingAdd]

@[step] theorem fixed.types.U68F60.saturating_sub_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.saturating_sub a b ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ := by
  simp only [fixed.types.U68F60.saturating_sub, spec_ok, Lusterna.Fixed.bits_val_saturatingSub]

@[step] theorem fixed.types.U68F60.saturating_mul_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.saturating_mul a b
    ⦃ r => r.bits.val = min (a.bits.val * b.bits.val / 2 ^ 60) (2 ^ 128 - 1) ⦄ := by
  simp only [fixed.types.U68F60.saturating_mul, spec_ok, Lusterna.Fixed.bits_val_saturatingMul]
