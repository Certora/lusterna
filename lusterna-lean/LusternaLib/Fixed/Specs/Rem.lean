module
public import LusternaLib.Fixed.Specs.Basic

/-!
# `@[step]` theorems of the remainder of `U68F60`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

theorem rem_spec (a b : U68F60) (h0 : b.bits.val ≠ 0) :
    rem a b ⦃ r => r.bits.val = a.bits.val % b.bits.val ⦄ := by
  have := bits_val_lt a
  have := Nat.mod_le a.bits.val b.bits.val
  unfold rem
  split
  · rename_i hb
    exact absurd hb h0
  · simp only [spec_ok]
    exact bits_val_wrap_of_lt (by simp only [val]; omega)

theorem rem_fspec (a b : U68F60) :
    rem a b ⦃ r => b.bits.val ≠ 0 ∧ r.bits.val = a.bits.val % b.bits.val ⦄fail := by
  apply fspec_of_spec_pre _ (rem_spec a b)
  intro r hr
  unfold rem at hr
  split at hr
  · simp at hr
  · assumption

end Lusterna.Fixed

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem_spec
    (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0) :
    fixed.types.U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem a b
    ⦃ r => r.bits.val = a.bits.val % b.bits.val ⦄ :=
  Lusterna.Fixed.rem_spec a b h0

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem_fspec
    (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem a b
    ⦃ r => b.bits.val ≠ 0 ∧ r.bits.val = a.bits.val % b.bits.val ⦄fail :=
  Lusterna.Fixed.rem_fspec a b

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem_spec
    (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0) :
    fixed.types.U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem a b
    ⦃ r => r.bits.val = a.bits.val % b.bits.val ⦄ :=
  Lusterna.Fixed.rem_spec a b h0

@[step] theorem fixed.types.U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem_fspec
    (a b : fixed.types.U68F60) :
    fixed.types.U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem a b
    ⦃ r => b.bits.val ≠ 0 ∧ r.bits.val = a.bits.val % b.bits.val ⦄fail :=
  Lusterna.Fixed.rem_fspec a b

@[step] theorem Shared0U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem_spec
    (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0) :
    Shared0U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem a b
    ⦃ r => r.bits.val = a.bits.val % b.bits.val ⦄ :=
  Lusterna.Fixed.rem_spec a b h0

@[step] theorem Shared0U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem_fspec
    (a b : fixed.types.U68F60) :
    Shared0U68F60.Insts.CoreOpsArithRemU68F60U68F60.rem a b
    ⦃ r => b.bits.val ≠ 0 ∧ r.bits.val = a.bits.val % b.bits.val ⦄fail :=
  Lusterna.Fixed.rem_fspec a b

@[step] theorem Shared1U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem_spec
    (a b : fixed.types.U68F60) (h0 : b.bits.val ≠ 0) :
    Shared1U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem a b
    ⦃ r => r.bits.val = a.bits.val % b.bits.val ⦄ :=
  Lusterna.Fixed.rem_spec a b h0

@[step] theorem Shared1U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem_fspec
    (a b : fixed.types.U68F60) :
    Shared1U68F60.Insts.CoreOpsArithRemShared0U68F60U68F60.rem a b
    ⦃ r => b.bits.val ≠ 0 ∧ r.bits.val = a.bits.val % b.bits.val ⦄fail :=
  Lusterna.Fixed.rem_fspec a b
