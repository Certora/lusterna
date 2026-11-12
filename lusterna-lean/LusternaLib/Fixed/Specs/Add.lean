module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the addition of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem add_spec (cfg : OverflowSettings) (a b : U68F60)
    (h : a.bits.val + b.bits.val < 2 ^ 128) :
    add cfg a b ⦃ r => r.bits.val = a.bits.val + b.bits.val ⦄ := by
  unfold add; apply wrapOrFail_spec _ h rfl

theorem add_fspec (cfg : OverflowSettings) [hc : Fact (cfg.overflowChecks = true)]
    (a b : U68F60) :
    add cfg a b ⦃ r => a.bits.val + b.bits.val < 2 ^ 128 ∧
      r.bits.val = a.bits.val + b.bits.val ⦄fail := by
  simp [add, hc.out]
  exact wrapOrFail_fspec

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddU68F60U68F60.add_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val + b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithAddU68F60U68F60.add a b
    ⦃ r => r.bits.val = a.bits.val + b.bits.val ⦄ :=
  Lusterna.Fixed.add_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddU68F60U68F60.add_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithAddU68F60U68F60.add a b
    ⦃ r => a.bits.val + b.bits.val < 2 ^ 128 ∧ r.bits.val = a.bits.val + b.bits.val ⦄fail :=
  Lusterna.Fixed.add_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val + b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add a b
    ⦃ r => r.bits.val = a.bits.val + b.bits.val ⦄ :=
  Lusterna.Fixed.add_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add a b
    ⦃ r => a.bits.val + b.bits.val < 2 ^ 128 ∧ r.bits.val = a.bits.val + b.bits.val ⦄fail :=
  Lusterna.Fixed.add_fspec s a b

@[step] theorem Shared0U68F60.Insts.CoreOpsArithAddU68F60U68F60.add_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val + b.bits.val < 2 ^ 128) :
    Shared0U68F60.Insts.CoreOpsArithAddU68F60U68F60.add a b
    ⦃ r => r.bits.val = a.bits.val + b.bits.val ⦄ :=
  Lusterna.Fixed.add_spec s a b h

@[step] theorem Shared0U68F60.Insts.CoreOpsArithAddU68F60U68F60.add_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    Shared0U68F60.Insts.CoreOpsArithAddU68F60U68F60.add a b
    ⦃ r => a.bits.val + b.bits.val < 2 ^ 128 ∧ r.bits.val = a.bits.val + b.bits.val ⦄fail :=
  Lusterna.Fixed.add_fspec s a b

@[step] theorem Shared1U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val + b.bits.val < 2 ^ 128) :
    Shared1U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add a b
    ⦃ r => r.bits.val = a.bits.val + b.bits.val ⦄ :=
  Lusterna.Fixed.add_spec s a b h

@[step] theorem Shared1U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    Shared1U68F60.Insts.CoreOpsArithAddShared0U68F60U68F60.add a b
    ⦃ r => a.bits.val + b.bits.val < 2 ^ 128 ∧ r.bits.val = a.bits.val + b.bits.val ⦄fail :=
  Lusterna.Fixed.add_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddAssignU68F60.add_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val + b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithAddAssignU68F60.add_assign a b
    ⦃ r => r.bits.val = a.bits.val + b.bits.val ⦄ :=
  Lusterna.Fixed.add_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddAssignU68F60.add_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithAddAssignU68F60.add_assign a b
    ⦃ r => a.bits.val + b.bits.val < 2 ^ 128 ∧ r.bits.val = a.bits.val + b.bits.val ⦄fail :=
  Lusterna.Fixed.add_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddAssignShared0U68F60.add_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60)
    (h : a.bits.val + b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithAddAssignShared0U68F60.add_assign a b
    ⦃ r => r.bits.val = a.bits.val + b.bits.val ⦄ :=
  Lusterna.Fixed.add_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithAddAssignShared0U68F60.add_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithAddAssignShared0U68F60.add_assign a b
    ⦃ r => a.bits.val + b.bits.val < 2 ^ 128 ∧ r.bits.val = a.bits.val + b.bits.val ⦄fail :=
  Lusterna.Fixed.add_fspec s a b
