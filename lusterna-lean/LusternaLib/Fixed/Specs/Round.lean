module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the rounding functions of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem bits_val_int (a : U68F60) : (int a).bits.val = a.bits.val / 2 ^ 60 * 2 ^ 60 := by
  have := bits_val_lt a
  exact bits_val_wrap_of_lt (show a.bits.val / 2 ^ 60 * 2 ^ 60 < 2 ^ 128 by omega)

theorem bits_val_frac (a : U68F60) : (frac a).bits.val = a.bits.val % 2 ^ 60 :=
  bits_val_wrap_of_lt (show a.bits.val % 2 ^ 60 < 2 ^ 128 by omega)

theorem ceil_spec (cfg : OverflowSettings) (a : U68F60)
    (h : a.bits.val % 2 ^ 60 = 0 ∨ a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) :
    ceil cfg a ⦃ r => r.bits.val = if a.bits.val % 2 ^ 60 = 0 then a.bits.val
      else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄ := by
  unfold ceil
  split
  · rename_i hm
    simp only [val, scale] at hm
    simp only [spec_ok, bits_val_int, hm, ↓reduceIte]
    exact Nat.div_mul_cancel (Nat.dvd_of_mod_eq_zero hm)
  · rename_i hm
    simp only [val, scale] at hm
    apply wrapOrFail_spec
    · simp only [val, bits_val_int, scale]
      exact h.resolve_left hm
    · simp only [val, bits_val_int, scale, hm, ↓reduceIte]

theorem ceil_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)]
    (a : U68F60) :
    ceil cfg a ⦃ r => (a.bits.val % 2 ^ 60 = 0 ∨ a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) ∧
      r.bits.val = if a.bits.val % 2 ^ 60 = 0 then a.bits.val
        else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄fail := by
  apply fspec_of_spec_pre _ (ceil_spec cfg a)
  intro r hr
  unfold ceil at hr
  split at hr
  · rename_i hm
    simp only [val, scale] at hm
    exact Or.inl hm
  · rw [hc.out] at hr
    have hb := lt_of_wrapOrFail_true hr
    simp only [val, bits_val_int, scale] at hb
    exact Or.inr hb

theorem round_spec (cfg : OverflowSettings) (a : U68F60)
    (h : a.bits.val % 2 ^ 60 < 2 ^ 59 ∨ a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) :
    round cfg a ⦃ r => r.bits.val = if a.bits.val % 2 ^ 60 < 2 ^ 59 then a.bits.val / 2 ^ 60 * 2 ^ 60
      else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄ := by
  have e : (2 : Nat) ^ 60 / 2 = 2 ^ 59 := by norm_num
  unfold round
  split
  · rename_i hm
    simp only [val, scale, e] at hm
    simp only [spec_ok, bits_val_int, hm, ↓reduceIte]
  · rename_i hm
    simp only [val, scale, e] at hm
    apply wrapOrFail_spec
    · simp only [val, bits_val_int, scale]
      exact h.resolve_left hm
    · simp only [val, bits_val_int, scale, hm, ↓reduceIte]

theorem round_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)]
    (a : U68F60) :
    round cfg a ⦃ r => (a.bits.val % 2 ^ 60 < 2 ^ 59 ∨
        a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) ∧
      r.bits.val = if a.bits.val % 2 ^ 60 < 2 ^ 59 then a.bits.val / 2 ^ 60 * 2 ^ 60
        else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄fail := by
  have e : (2 : Nat) ^ 60 / 2 = 2 ^ 59 := by norm_num
  apply fspec_of_spec_pre _ (round_spec cfg a)
  intro r hr
  unfold round at hr
  split at hr
  · rename_i hm
    simp only [val, scale, e] at hm
    exact Or.inl hm
  · rw [hc.out] at hr
    have hb := lt_of_wrapOrFail_true hr
    simp only [val, bits_val_int, scale] at hb
    exact Or.inr hb

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.int_spec (a : fixed.types.U68F60) :
    fixed.types.U68F60.int a ⦃ r => r.bits.val = a.bits.val / 2 ^ 60 * 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.int, spec_ok, Lusterna.Fixed.bits_val_int]

@[step] theorem fixed.types.U68F60.floor_spec (a : fixed.types.U68F60) :
    fixed.types.U68F60.floor a ⦃ r => r.bits.val = a.bits.val / 2 ^ 60 * 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.floor, spec_ok, Lusterna.Fixed.bits_val_int]

@[step] theorem fixed.types.U68F60.frac_spec (a : fixed.types.U68F60) :
    fixed.types.U68F60.frac a ⦃ r => r.bits.val = a.bits.val % 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.frac, spec_ok, Lusterna.Fixed.bits_val_frac]

@[step] theorem fixed.types.U68F60.ceil_spec [s : Lusterna.OverflowSettings]
    (a : fixed.types.U68F60)
    (h : a.bits.val % 2 ^ 60 = 0 ∨ a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) :
    fixed.types.U68F60.ceil a ⦃ r => r.bits.val = if a.bits.val % 2 ^ 60 = 0 then a.bits.val
      else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄ :=
  Lusterna.Fixed.ceil_spec s a h

@[step] theorem fixed.types.U68F60.ceil_fspec [s : Lusterna.OverflowSettings]
    [Fact (s.debugAssertions = true)] (a : fixed.types.U68F60) :
    fixed.types.U68F60.ceil a
    ⦃ r => (a.bits.val % 2 ^ 60 = 0 ∨ a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) ∧
      r.bits.val = if a.bits.val % 2 ^ 60 = 0 then a.bits.val
        else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.ceil_fspec s a

@[step] theorem fixed.types.U68F60.round_spec [s : Lusterna.OverflowSettings]
    (a : fixed.types.U68F60)
    (h : a.bits.val % 2 ^ 60 < 2 ^ 59 ∨ a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) :
    fixed.types.U68F60.round a ⦃ r => r.bits.val = if a.bits.val % 2 ^ 60 < 2 ^ 59
      then a.bits.val / 2 ^ 60 * 2 ^ 60 else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄ :=
  Lusterna.Fixed.round_spec s a h

@[step] theorem fixed.types.U68F60.round_fspec [s : Lusterna.OverflowSettings]
    [Fact (s.debugAssertions = true)] (a : fixed.types.U68F60) :
    fixed.types.U68F60.round a
    ⦃ r => (a.bits.val % 2 ^ 60 < 2 ^ 59 ∨ a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 < 2 ^ 128) ∧
      r.bits.val = if a.bits.val % 2 ^ 60 < 2 ^ 59 then a.bits.val / 2 ^ 60 * 2 ^ 60
        else a.bits.val / 2 ^ 60 * 2 ^ 60 + 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.round_fspec s a
