module
public import LusternaLib.Fixed.Fraction
public import LusternaLib.Fixed.Glue
public import LusternaSpec.FSpecScalar

/-!
# Facts about the model of `U68F60` that the `@[step]` theorems use
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Fixed

/-- `b = b` for a `Bool`. Instance search proves `Fact (s.overflowChecks = true)` with it when the
instance `s` sets `overflowChecks` to `true`. -/
instance factBoolEq {b : Bool} : Fact (b = b) := ⟨rfl⟩

theorem bits_val_wrap (n : Nat) : (wrap n).bits.val = n % 2 ^ 128 := by
  simp only [wrap, UScalar.val, BitVec.toNat_ofNat, UScalarTy.numBits]

theorem bits_val_wrap_of_lt {n : Nat} (h : n < 2 ^ 128) : (wrap n).bits.val = n :=
  val_wrap_of_lt h

theorem bits_val_lt (a : U68F60) : a.bits.val < 2 ^ 128 := val_lt a

theorem wrapOrFail_spec (flag : Bool) {n m : Nat} (h : n < 2 ^ 128) (he : n = m) :
    wrapOrFail flag n ⦃ r => r.bits.val = m ⦄ := by
  simp[wrapOrFail]
  split_ifs with h_if
  · simp only [spec_ok]
    rw [← he]
    exact bits_val_wrap_of_lt h
  · grind

/-- If `wrapOrFail true n` returns, `n` fits in 128 bits. -/
theorem lt_of_wrapOrFail_true {n : Nat} {r : U68F60} (h : wrapOrFail true n = ok r) :
    n < 2 ^ 128 := by
  simp [wrapOrFail] at h
  split_ifs at h with h_if
  · grind
  · simp at h;

theorem wrapOrFail_fspec {n : Nat} :
    wrapOrFail true n ⦃ r => n < 2 ^ 128 ∧ r.bits.val = n ⦄fail :=
  fspec_of_spec_pre (fun _ h => lt_of_wrapOrFail_true h) (fun h => wrapOrFail_spec true h rfl)

end Lusterna.Fixed
