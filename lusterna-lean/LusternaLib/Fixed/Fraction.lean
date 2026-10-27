module
public import LusternaLib.Fixed.Model
public import Solanalib.Numeric.Fraction

/-!
# `U68F60` as `Solanalib.Fraction 60`

`toFraction` maps a `U68F60` to the `Solanalib.Fraction 60` with the same
bits. When an operation succeeds and its exact result fits in 128 bits, its
result maps to the result of the `Fraction` operation with the same
rounding: addition and subtraction are exact, multiplication and division
round down.
-/

@[expose] public section

open Aeneas Aeneas.Std Result
open Solanalib.Fraction

namespace Lusterna.Fixed

/-- The `Fraction 60` with the bits of `a`. -/
def toFraction (a : U68F60) : Solanalib.Fraction 60 := ⟨val a⟩

theorem val_wrap_of_lt {n : Nat} (h : n < 2 ^ 128) : val (wrap n) = n := by
  simp only [val, wrap, UScalar.val, BitVec.toNat_ofNat]
  exact Nat.mod_eq_of_lt (by simpa using h)

theorem val_lt (a : U68F60) : val a < 2 ^ 128 := by
  simpa [val] using a.bits.bv.isLt

theorem eq_wrap_of_wrapOrFail {flag : Bool} {n : Nat} {r : U68F60}
    (h : wrapOrFail flag n = ok r) : r = wrap n := by
  unfold wrapOrFail at h
  split at h <;> simp_all

theorem toFraction_add {c : OverflowSettings} {a b r : U68F60} (h : add c a b = ok r)
    (hfit : val a + val b < 2 ^ 128) : toFraction r = toFraction a + toFraction b := by
  obtain rfl := eq_wrap_of_wrapOrFail h
  ext
  simp [toFraction, val_wrap_of_lt hfit]

theorem toFraction_sub {c : OverflowSettings} {a b r : U68F60} (h : sub c a b = ok r)
    (hle : val b ≤ val a) : toFraction r = toFraction a - toFraction b := by
  unfold sub at h
  simp only [hle, ite_true, Result.ok.injEq] at h
  subst h
  ext
  simp only [toFraction, bits_sub]
  rw [val_wrap_of_lt (by have := val_lt a; omega)]
  push_cast [hle]
  rfl

theorem toFraction_mul {c : OverflowSettings} {a b r : U68F60} (h : mul c a b = ok r)
    (hfit : val a * val b / scale < 2 ^ 128) :
    toFraction r = toFraction a *↓ toFraction b := by
  obtain rfl := eq_wrap_of_wrapOrFail h
  ext
  simp only [toFraction, val_wrap_of_lt hfit, bits_mulFloor]
  rw [Int.fdiv_eq_ediv_of_nonneg _ (by positivity)]
  norm_cast

theorem toFraction_div {c : OverflowSettings} {a b r : U68F60} (h : div c a b = ok r)
    (hfit : val a * scale / val b < 2 ^ 128) :
    toFraction r = toFraction a /↓ toFraction b := by
  unfold div at h
  split at h
  · simp at h
  · obtain rfl := eq_wrap_of_wrapOrFail h
    ext
    simp only [toFraction, val_wrap_of_lt hfit, bits_divFloor]
    rw [Int.fdiv_eq_ediv_of_nonneg _ (by positivity)]
    norm_cast

end Lusterna.Fixed
