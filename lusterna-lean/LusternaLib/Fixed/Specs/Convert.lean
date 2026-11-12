module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the conversions of `U68F60` from and to bits, `bool` and itself
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem constFromInt_spec (n : U128) (h : n.val < 2 ^ 68) :
    constFromInt n ⦃ r => r.bits.val = n.val * 2 ^ 60 ⦄ := by
  unfold constFromInt
  split
  · simp only [spec_ok]
    exact bits_val_wrap_of_lt (show n.val * 2 ^ 60 < 2 ^ 128 by omega)
  · rename_i hn
    exact absurd h hn

theorem constFromInt_fspec (n : U128) :
    constFromInt n ⦃ r => n.val < 2 ^ 68 ∧ r.bits.val = n.val * 2 ^ 60 ⦄fail := by
  apply fspec_of_spec_pre _ (constFromInt_spec n)
  intro r hr
  unfold constFromInt at hr
  split at hr
  · assumption
  · simp at hr

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.from_bits_spec (n : U128) :
    fixed.types.U68F60.from_bits n ⦃ r => r.bits = n ⦄ := by
  simp only [fixed.types.U68F60.from_bits, spec_ok]

@[step] theorem fixed.types.U68F60.to_bits_spec (a : fixed.types.U68F60) :
    fixed.types.U68F60.to_bits a ⦃ r => r = a.bits ⦄ := by
  simp only [fixed.types.U68F60.to_bits, spec_ok]

@[step] theorem fixed.types.U68F60.const_from_int_spec (n : U128) (h : n.val < 2 ^ 68) :
    fixed.types.U68F60.const_from_int n ⦃ r => r.bits.val = n.val * 2 ^ 60 ⦄ :=
  Lusterna.Fixed.constFromInt_spec n h

@[step] theorem fixed.types.U68F60.const_from_int_fspec (n : U128) :
    fixed.types.U68F60.const_from_int n
    ⦃ r => n.val < 2 ^ 68 ∧ r.bits.val = n.val * 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.constFromInt_fspec n

@[step] theorem fixed.types.U68F60.Insts.CoreConvertFromBool.from_spec (b : Bool) :
    fixed.types.U68F60.Insts.CoreConvertFromBool.from b
    ⦃ r => r.bits.val = if b then 2 ^ 60 else 0 ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreConvertFromBool.from, spec_ok]
  cases b <;> simp [Lusterna.Fixed.bits_val_wrap, Lusterna.Fixed.scale]

@[step] theorem fixed.types.U68F60.Insts.CoreConvertFromU8.from_spec (n : U8) :
    fixed.types.U68F60.Insts.CoreConvertFromU8.from n ⦃ r => r.bits.val = n.val * 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreConvertFromU8.from, spec_ok]
  exact Lusterna.Fixed.bits_val_wrap_of_lt (by simp only [Lusterna.Fixed.scale]; scalar_tac)

@[step] theorem fixed.types.U68F60.Insts.CoreConvertFromU16.from_spec (n : U16) :
    fixed.types.U68F60.Insts.CoreConvertFromU16.from n ⦃ r => r.bits.val = n.val * 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreConvertFromU16.from, spec_ok]
  exact Lusterna.Fixed.bits_val_wrap_of_lt (by simp only [Lusterna.Fixed.scale]; scalar_tac)

@[step] theorem fixed.types.U68F60.Insts.CoreConvertFromU32.from_spec (n : U32) :
    fixed.types.U68F60.Insts.CoreConvertFromU32.from n ⦃ r => r.bits.val = n.val * 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreConvertFromU32.from, spec_ok]
  exact Lusterna.Fixed.bits_val_wrap_of_lt (by simp only [Lusterna.Fixed.scale]; scalar_tac)

@[step] theorem fixed.types.U68F60.Insts.CoreConvertFromU64.from_spec (n : U64) :
    fixed.types.U68F60.Insts.CoreConvertFromU64.from n ⦃ r => r.bits.val = n.val * 2 ^ 60 ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreConvertFromU64.from, spec_ok]
  exact Lusterna.Fixed.bits_val_wrap_of_lt (by simp only [Lusterna.Fixed.scale]; scalar_tac)

@[step] theorem fixed.types.U68F60.Insts.FixedTraitsToFixed.to_fixed_spec
    (a : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.FixedTraitsToFixed.to_fixed a ⦃ r => r = a ⦄ := by
  simp only [fixed.types.U68F60.Insts.FixedTraitsToFixed.to_fixed, spec_ok]

@[step] theorem fixed.types.U68F60.Insts.FixedTraitsToFixed.checked_to_fixed_spec
    (a : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.FixedTraitsToFixed.checked_to_fixed a ⦃ r => r = some a ⦄ := by
  simp only [fixed.types.U68F60.Insts.FixedTraitsToFixed.checked_to_fixed, spec_ok]

@[step] theorem fixed.types.U68F60.Insts.FixedTraitsFromFixed.from_fixed_spec
    (a : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.FixedTraitsFromFixed.from_fixed a ⦃ r => r = a ⦄ := by
  simp only [fixed.types.U68F60.Insts.FixedTraitsFromFixed.from_fixed, spec_ok]

@[step] theorem fixed.types.U68F60.Insts.FixedTraitsFromFixed.checked_from_fixed_spec
    (a : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.FixedTraitsFromFixed.checked_from_fixed a ⦃ r => r = some a ⦄ := by
  simp only [fixed.types.U68F60.Insts.FixedTraitsFromFixed.checked_from_fixed, spec_ok]

@[step] theorem fixed.types.U68F60.Insts.FixedTraitsFromFixed.saturating_from_fixed_spec
    (a : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.FixedTraitsFromFixed.saturating_from_fixed a ⦃ r => r = a ⦄ := by
  simp only [fixed.types.U68F60.Insts.FixedTraitsFromFixed.saturating_from_fixed, spec_ok]
