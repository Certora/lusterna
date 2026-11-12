module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the division of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem div_spec (cfg : OverflowSettings) (a b : U68F60) (h0 : b.bits.val ≠ 0)
    (h : a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) :
    div cfg a b ⦃ r => r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄ := by
  unfold div
  split
  · rename_i hb
    exact absurd hb h0
  · exact wrapOrFail_spec _ h rfl

theorem div_fspec (cfg : OverflowSettings) [hc : Fact (cfg.debugAssertions = true)]
    (a b : U68F60) :
    div cfg a b ⦃ r => (b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) ∧
      r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄fail := by
  apply fspec_of_spec_pre _ (fun h => div_spec cfg a b h.1 h.2)
  intro r hr
  unfold div at hr
  split at hr
  · simp at hr
  · rw [hc.out] at hr
    exact ⟨by assumption, lt_of_wrapOrFail_true hr⟩

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivU68F60U68F60.div_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0)
    (h : a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithDivU68F60U68F60.div a b
    ⦃ r => r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄ :=
  Lusterna.Fixed.div_spec s a b h0 h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivU68F60U68F60.div_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithDivU68F60U68F60.div a b
    ⦃ r => (b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) ∧
      r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄fail :=
  Lusterna.Fixed.div_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0)
    (h : a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div a b
    ⦃ r => r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄ :=
  Lusterna.Fixed.div_spec s a b h0 h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div a b
    ⦃ r => (b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) ∧
      r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄fail :=
  Lusterna.Fixed.div_fspec s a b

@[step] theorem Shared0U68F60.Insts.CoreOpsArithDivU68F60U68F60.div_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0)
    (h : a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) :
    Shared0U68F60.Insts.CoreOpsArithDivU68F60U68F60.div a b
    ⦃ r => r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄ :=
  Lusterna.Fixed.div_spec s a b h0 h

@[step] theorem Shared0U68F60.Insts.CoreOpsArithDivU68F60U68F60.div_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    Shared0U68F60.Insts.CoreOpsArithDivU68F60U68F60.div a b
    ⦃ r => (b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) ∧
      r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄fail :=
  Lusterna.Fixed.div_fspec s a b

@[step] theorem Shared1U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0)
    (h : a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) :
    Shared1U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div a b
    ⦃ r => r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄ :=
  Lusterna.Fixed.div_spec s a b h0 h

@[step] theorem Shared1U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    Shared1U68F60.Insts.CoreOpsArithDivShared0U68F60U68F60.div a b
    ⦃ r => (b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) ∧
      r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄fail :=
  Lusterna.Fixed.div_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivAssignU68F60.div_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0)
    (h : a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithDivAssignU68F60.div_assign a b
    ⦃ r => r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄ :=
  Lusterna.Fixed.div_spec s a b h0 h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivAssignU68F60.div_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithDivAssignU68F60.div_assign a b
    ⦃ r => (b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) ∧
      r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄fail :=
  Lusterna.Fixed.div_fspec s a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivAssignShared0U68F60.div_assign_spec
    [s : Lusterna.OverflowSettings] (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0)
    (h : a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) :
    fixed.types.U68F60.Insts.CoreOpsArithDivAssignShared0U68F60.div_assign a b
    ⦃ r => r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄ :=
  Lusterna.Fixed.div_spec s a b h0 h

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithDivAssignShared0U68F60.div_assign_fspec
    [s : Lusterna.OverflowSettings] [Fact (s.debugAssertions = true)] (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithDivAssignShared0U68F60.div_assign a b
    ⦃ r => (b.bits.val ≠ 0 ∧ a.bits.val * 2 ^ 60 / b.bits.val < 2 ^ 128) ∧
      r.bits.val = a.bits.val * 2 ^ 60 / b.bits.val ⦄fail :=
  Lusterna.Fixed.div_fspec s a b
