module
public import LusternaLib.Uint.Glue
public import LusternaSpec.FSpecScalar

/-!
# Facts about the model of the numbers of `uint` that the `@[step]` theorems use
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec

namespace Lusterna.Uint

private theorem limbsVal_ofFn (k m : Nat) :
    limbsVal (List.ofFn fun (i : Fin k) => (⟨BitVec.ofNat _ (m / 2 ^ (64 * i.val))⟩ : U64)) =
      m % 2 ^ (64 * k) := by
  induction k generalizing m with
  | zero => simp [limbsVal, Nat.mod_one]
  | succ k ih =>
    rw [List.ofFn_succ]
    simp only [limbsVal, Fin.val_zero, Fin.val_succ]
    have e : ∀ i : Nat, m / 2 ^ (64 * (i + 1)) = m / 2 ^ 64 / 2 ^ (64 * i) := by
      intro i
      rw [Nat.div_div_eq_div_mul, ← Nat.pow_add]
      congr 2
      ring
    simp only [e]
    rw [ih (m / 2 ^ 64)]
    simp only [UScalar.val, BitVec.toNat_ofNat, UScalarTy.numBits, Nat.mul_zero, Nat.pow_zero,
      Nat.div_one]
    rw [show 64 * (k + 1) = 64 + 64 * k by ring, Nat.pow_add, Nat.mod_mul]

theorem val_ofNat (n : Usize) (m : Nat) : val (ofNat n m) = m % modulus n := by
  simp only [val, ofNat, Array.from_val, modulus]
  exact limbsVal_ofFn n.val m

theorem val_ofNat_of_lt {n : Usize} {m : Nat} (h : m < modulus n) : val (ofNat n m) = m := by
  rw [val_ofNat]
  exact Nat.mod_eq_of_lt h

theorem limbsVal_lt (l : List U64) : limbsVal l < 2 ^ (64 * l.length) := by
  induction l with
  | nil => simp [limbsVal]
  | cons x l ih =>
    simp only [limbsVal, List.length_cons]
    have hx : x.val < 2 ^ 64 := by simpa using x.hBounds
    have hl : 2 ^ 64 * limbsVal l + 2 ^ 64 ≤ 2 ^ 64 * 2 ^ (64 * l.length) := by
      rw [← Nat.mul_succ]
      exact Nat.mul_le_mul_left _ ih
    rw [show 64 * (l.length + 1) = 64 + 64 * l.length by ring, Nat.pow_add]
    omega

theorem val_lt {n : Usize} (a : Array U64 n) : val a < modulus n := by
  have := limbsVal_lt a.val
  simpa [val, modulus] using this

theorem ofNatOrFail_spec (n : Usize) {m : Nat} (h : m < modulus n) :
    ofNatOrFail n m ⦃ r => val r = m ⦄ := by
  simp only [ofNatOrFail, h, ↓reduceIte, spec_ok]
  exact val_ofNat_of_lt h

theorem ofNatOrFail_fspec (n : Usize) (m : Nat) :
    ofNatOrFail n m ⦃ r => m < modulus n ∧ val r = m ⦄fail := by
  apply fspec_of_spec_pre _ (ofNatOrFail_spec n)
  intro r hr
  unfold ofNatOrFail at hr
  split at hr
  · assumption
  · simp at hr

end Lusterna.Uint
