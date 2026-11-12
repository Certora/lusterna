module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the multiplication and division of `U68F60` by a `u128`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem mulInt_spec (cfg : OverflowSettings) (a : U68F60) (n : U128)
    (h : a.bits.val * n.val < 2 ^ 128) :
    mulInt cfg a n ⦃ r => r.bits.val = a.bits.val * n.val ⦄ :=
  wrapOrFail_spec _ h rfl

theorem mulInt_fspec (cfg : OverflowSettings) [hc : Fact (cfg.overflowChecks = true)]
    (a : U68F60) (n : U128) :
    mulInt cfg a n ⦃ r => a.bits.val * n.val < 2 ^ 128 ∧ r.bits.val = a.bits.val * n.val ⦄fail := by
  unfold mulInt
  rw [hc.out]
  exact wrapOrFail_fspec

theorem divInt_spec (a : U68F60) (n : U128) (h0 : n.val ≠ 0) :
    divInt a n ⦃ r => r.bits.val = a.bits.val / n.val ⦄ := by
  have := bits_val_lt a
  have := Nat.div_le_self a.bits.val n.val
  unfold divInt
  split
  · rename_i hn
    exact absurd hn h0
  · simp only [spec_ok]
    exact bits_val_wrap_of_lt (by simp only [val]; omega)

theorem divInt_fspec (a : U68F60) (n : U128) :
    divInt a n ⦃ r => n.val ≠ 0 ∧ r.bits.val = a.bits.val / n.val ⦄fail := by
  apply fspec_of_spec_pre _ (divInt_spec a n)
  intro r hr
  unfold divInt at hr
  split at hr
  · simp at hr
  · assumption

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulU128U68F60.mul_spec
    [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) (n : U128)
    (h : a.bits.val * n.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulU128U68F60.mul a n
    ⦃ r => r.bits.val = a.bits.val * n.val ⦄ :=
  Lusterna.Fixed.mulInt_spec s a n h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulU128U68F60.mul_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)]
    (a : fixed.types.U68F60) (n : U128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulU128U68F60.mul a n
    ⦃ r => a.bits.val * n.val < 2 ^ 128 ∧ r.bits.val = a.bits.val * n.val ⦄fail :=
  Lusterna.Fixed.mulInt_fspec s a n

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulAssignU128.mul_assign_spec
    [s : Lusterna.OverflowSettings] (a : fixed.types.U68F60) (n : U128)
    (h : a.bits.val * n.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulAssignU128.mul_assign a n
    ⦃ r => r.bits.val = a.bits.val * n.val ⦄ :=
  Lusterna.Fixed.mulInt_spec s a n h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulAssignU128.mul_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)]
    (a : fixed.types.U68F60) (n : U128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulAssignU128.mul_assign a n
    ⦃ r => a.bits.val * n.val < 2 ^ 128 ∧ r.bits.val = a.bits.val * n.val ⦄fail :=
  Lusterna.Fixed.mulInt_fspec s a n

@[step] theorem U128.Insts.CoreOpsArithMulU68F60U68F60.mul_spec
    [s : Lusterna.OverflowSettings] (n : U128) (a : fixed.types.U68F60)
    (h : a.bits.val * n.val < 2 ^ 128) :
    U128.Insts.CoreOpsArithMulU68F60U68F60.mul n a
    ⦃ r => r.bits.val = a.bits.val * n.val ⦄ :=
  Lusterna.Fixed.mulInt_spec s a n h

@[step] theorem U128.Insts.CoreOpsArithMulU68F60U68F60.mul_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)]
    (n : U128) (a : fixed.types.U68F60) :
    U128.Insts.CoreOpsArithMulU68F60U68F60.mul n a
    ⦃ r => a.bits.val * n.val < 2 ^ 128 ∧ r.bits.val = a.bits.val * n.val ⦄fail :=
  Lusterna.Fixed.mulInt_fspec s a n

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivU128U68F60.div_spec
    (a : fixed.types.U68F60) (n : U128) (h0 : n.val ≠ 0) :
    fixed.types.U68F60.Insts.CoreOpsArithDivU128U68F60.div a n
    ⦃ r => r.bits.val = a.bits.val / n.val ⦄ :=
  Lusterna.Fixed.divInt_spec a n h0

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivU128U68F60.div_fspec
    (a : fixed.types.U68F60) (n : U128) :
    fixed.types.U68F60.Insts.CoreOpsArithDivU128U68F60.div a n
    ⦃ r => n.val ≠ 0 ∧ r.bits.val = a.bits.val / n.val ⦄fail :=
  Lusterna.Fixed.divInt_fspec a n

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivAssignU128.div_assign_spec
    (a : fixed.types.U68F60) (n : U128) (h0 : n.val ≠ 0) :
    fixed.types.U68F60.Insts.CoreOpsArithDivAssignU128.div_assign a n
    ⦃ r => r.bits.val = a.bits.val / n.val ⦄ :=
  Lusterna.Fixed.divInt_spec a n h0

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivAssignU128.div_assign_fspec
    (a : fixed.types.U68F60) (n : U128) :
    fixed.types.U68F60.Insts.CoreOpsArithDivAssignU128.div_assign a n
    ⦃ r => n.val ≠ 0 ∧ r.bits.val = a.bits.val / n.val ⦄fail :=
  Lusterna.Fixed.divInt_fspec a n

@[step] theorem fixed.types.U68F60.unwrapped_div_int_spec
    (a : fixed.types.U68F60) (n : U128) (h0 : n.val ≠ 0) :
    fixed.types.U68F60.unwrapped_div_int a n ⦃ r => r.bits.val = a.bits.val / n.val ⦄ :=
  Lusterna.Fixed.divInt_spec a n h0

@[step] theorem fixed.types.U68F60.unwrapped_div_int_fspec (a : fixed.types.U68F60) (n : U128) :
    fixed.types.U68F60.unwrapped_div_int a n
    ⦃ r => n.val ≠ 0 ∧ r.bits.val = a.bits.val / n.val ⦄fail :=
  Lusterna.Fixed.divInt_fspec a n
