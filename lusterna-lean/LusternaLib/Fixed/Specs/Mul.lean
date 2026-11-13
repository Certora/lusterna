module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the multiplication of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem mul_spec (cfg : OverflowSettings) (a b : U68F60)
    (h : a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128) :
    mul cfg a b ⦃ r => r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄ :=
  wrapOrFail_spec _ h rfl

theorem mul_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)]
    (a b : U68F60) :
    mul cfg a b ⦃ r => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
      r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄fail := by
  unfold mul
  rw [hc.out]
  exact wrapOrFail_fspec

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul a b
    ⦃ r => r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄ :=
  Lusterna.Fixed.mul_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul a b
    ⦃ r => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
      r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.mul_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul a b
    ⦃ r => r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄ :=
  Lusterna.Fixed.mul_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul a b
    ⦃ r => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
      r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.mul_fspec s a b

@[step] theorem Shared0U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128) :
    Shared0U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul a b
    ⦃ r => r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄ :=
  Lusterna.Fixed.mul_spec s a b h

@[step] theorem Shared0U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    Shared0U68F60.Insts.CoreOpsArithMulU68F60U68F60.mul a b
    ⦃ r => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
      r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.mul_fspec s a b

@[step] theorem Shared1U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128) :
    Shared1U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul a b
    ⦃ r => r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄ :=
  Lusterna.Fixed.mul_spec s a b h

@[step] theorem Shared1U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    Shared1U68F60.Insts.CoreOpsArithMulShared0U68F60U68F60.mul a b
    ⦃ r => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
      r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.mul_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulAssignU68F60.mul_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulAssignU68F60.mul_assign a b
    ⦃ r => r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄ :=
  Lusterna.Fixed.mul_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulAssignU68F60.mul_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithMulAssignU68F60.mul_assign a b
    ⦃ r => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
      r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.mul_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulAssignShared0U68F60.mul_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithMulAssignShared0U68F60.mul_assign a b
    ⦃ r => r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄ :=
  Lusterna.Fixed.mul_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithMulAssignShared0U68F60.mul_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithMulAssignShared0U68F60.mul_assign a b
    ⦃ r => a.bits.val * b.bits.val / 2 ^ 60 < 2 ^ 128 ∧
      r.bits.val = a.bits.val * b.bits.val / 2 ^ 60 ⦄fail :=
  Lusterna.Fixed.mul_fspec s a b
