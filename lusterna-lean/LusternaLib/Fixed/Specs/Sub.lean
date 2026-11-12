module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the subtraction of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem sub_spec (cfg : OverflowSettings) (a b : U68F60) (h : b.bits.val ≤ a.bits.val) :
    sub cfg a b ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ := by
  have := bits_val_lt a
  unfold sub
  split
  · simp only [spec_ok, val]
    exact bits_val_wrap_of_lt (by omega)
  · rename_i hn
    exact absurd h hn

theorem sub_fspec (cfg : OverflowSettings) [hc : Fact (cfg.overflowChecks = true)]
    (a b : U68F60) :
    sub cfg a b ⦃ r => b.bits.val ≤ a.bits.val ∧
      r.bits.val = a.bits.val - b.bits.val ⦄fail := by
  apply fspec_of_spec_pre _ (sub_spec cfg a b)
  intro r hr
  unfold sub at hr
  split at hr
  · assumption
  · simp [hc.out] at hr

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h : b.bits.val ≤ a.bits.val) :
    fixed.types.U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub a b
    ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ :=
  Lusterna.Fixed.sub_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub a b
    ⦃ r => b.bits.val ≤ a.bits.val ∧ r.bits.val = a.bits.val - b.bits.val ⦄fail :=
  Lusterna.Fixed.sub_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h : b.bits.val ≤ a.bits.val) :
    fixed.types.U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub a b
    ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ :=
  Lusterna.Fixed.sub_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub a b
    ⦃ r => b.bits.val ≤ a.bits.val ∧ r.bits.val = a.bits.val - b.bits.val ⦄fail :=
  Lusterna.Fixed.sub_fspec s a b

@[step] theorem Shared0U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h : b.bits.val ≤ a.bits.val) :
    Shared0U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub a b
    ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ :=
  Lusterna.Fixed.sub_spec s a b h

@[step] theorem Shared0U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    Shared0U68F60.Insts.CoreOpsArithSubU68F60U68F60.sub a b
    ⦃ r => b.bits.val ≤ a.bits.val ∧ r.bits.val = a.bits.val - b.bits.val ⦄fail :=
  Lusterna.Fixed.sub_fspec s a b

@[step] theorem Shared1U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h : b.bits.val ≤ a.bits.val) :
    Shared1U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub a b
    ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ :=
  Lusterna.Fixed.sub_spec s a b h

@[step] theorem Shared1U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    Shared1U68F60.Insts.CoreOpsArithSubShared0U68F60U68F60.sub a b
    ⦃ r => b.bits.val ≤ a.bits.val ∧ r.bits.val = a.bits.val - b.bits.val ⦄fail :=
  Lusterna.Fixed.sub_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubAssignU68F60.sub_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h : b.bits.val ≤ a.bits.val) :
    fixed.types.U68F60.Insts.CoreOpsArithSubAssignU68F60.sub_assign a b
    ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ :=
  Lusterna.Fixed.sub_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubAssignU68F60.sub_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithSubAssignU68F60.sub_assign a b
    ⦃ r => b.bits.val ≤ a.bits.val ∧ r.bits.val = a.bits.val - b.bits.val ⦄fail :=
  Lusterna.Fixed.sub_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubAssignShared0U68F60.sub_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h : b.bits.val ≤ a.bits.val) :
    fixed.types.U68F60.Insts.CoreOpsArithSubAssignShared0U68F60.sub_assign a b
    ⦃ r => r.bits.val = a.bits.val - b.bits.val ⦄ :=
  Lusterna.Fixed.sub_spec s a b h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithSubAssignShared0U68F60.sub_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.overflowChecks = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithSubAssignShared0U68F60.sub_assign a b
    ⦃ r => b.bits.val ≤ a.bits.val ∧ r.bits.val = a.bits.val - b.bits.val ⦄fail :=
  Lusterna.Fixed.sub_fspec s a b
