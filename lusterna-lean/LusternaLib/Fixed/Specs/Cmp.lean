module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the comparisons of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP

@[step] theorem fixed.types.U68F60.is_zero_spec (a : fixed.types.U68F60) :
    fixed.types.U68F60.is_zero a ⦃ r => r = decide (a.bits.val = 0) ⦄ := by
  simp only [fixed.types.U68F60.is_zero, spec_ok]
  exact decide_eq_decide.mpr Iff.rfl

@[step] theorem fixed.types.U68F60.Insts.CoreCmpPartialEqU68F60.eq_spec
    (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreCmpPartialEqU68F60.eq a b
    ⦃ r => r = decide (a.bits.val = b.bits.val) ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreCmpPartialEqU68F60.eq, spec_ok]
  exact decide_eq_decide.mpr Iff.rfl

@[step] theorem fixed.types.U68F60.Insts.CoreCmpPartialOrdU68F60.partial_cmp_spec
    (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreCmpPartialOrdU68F60.partial_cmp a b
    ⦃ r => r = some (compare a.bits.val b.bits.val) ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreCmpPartialOrdU68F60.partial_cmp, spec_ok,
    Lusterna.Fixed.val]

@[step] theorem fixed.types.U68F60.Insts.CoreCmpOrd.cmp_spec (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreCmpOrd.cmp a b ⦃ r => r = compare a.bits.val b.bits.val ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreCmpOrd.cmp, spec_ok, Lusterna.Fixed.val]

@[step] theorem fixed.types.U68F60.Insts.CoreCmpPartialEqU64.eq_spec
    (a : fixed.types.U68F60) (n : U64) :
    fixed.types.U68F60.Insts.CoreCmpPartialEqU64.eq a n
    ⦃ r => r = decide (a.bits.val = n.val * 2 ^ 60) ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreCmpPartialEqU64.eq, spec_ok]
  exact decide_eq_decide.mpr Iff.rfl

@[step] theorem fixed.types.U68F60.Insts.CoreCmpPartialOrdU64.partial_cmp_spec
    (a : fixed.types.U68F60) (n : U64) :
    fixed.types.U68F60.Insts.CoreCmpPartialOrdU64.partial_cmp a n
    ⦃ r => r = some (compare a.bits.val (n.val * 2 ^ 60)) ⦄ := by
  simp only [fixed.types.U68F60.Insts.CoreCmpPartialOrdU64.partial_cmp, spec_ok,
    Lusterna.Fixed.val, Lusterna.Fixed.scale]
