module
public import LusternaLib.Uint.Specs.Basic

/-!
# `@[step]` theorems of the arithmetic of the numbers of `uint`
-/

@[expose] public section

open Aeneas Aeneas.Std Result WP
open Lusterna.Spec Lusterna.Uint

namespace Lusterna.Uint

theorem divMod_spec {n : Usize} (a b : Array U64 n) (h0 : val b ≠ 0) :
    divMod a b ⦃ r => val r.1 = val a / val b ∧ val r.2 = val a % val b ⦄ := by
  have := val_lt a
  have := Nat.div_le_self (val a) (val b)
  have := Nat.mod_le (val a) (val b)
  simp only [divMod, h0, ↓reduceIte, spec_ok]
  exact ⟨val_ofNat_of_lt (by omega), val_ofNat_of_lt (by omega)⟩

/-- If `divMod a b` returns, `b` is not zero. -/
theorem ne_zero_of_divMod {n : Usize} {a b : Array U64 n} {r} (h : divMod a b = ok r) :
    val b ≠ 0 := by
  unfold divMod at h
  split at h
  · simp at h
  · assumption

end Lusterna.Uint

@[step] theorem uint.add_spec {N : Usize} (a b : Array U64 N) (h : val a + val b < modulus N) :
    uint.add a b ⦃ r => val r = val a + val b ⦄ :=
  ofNatOrFail_spec N h

@[step] theorem uint.add_fspec {N : Usize} (a b : Array U64 N) :
    uint.add a b ⦃ r => val a + val b < modulus N ∧ val r = val a + val b ⦄fail :=
  ofNatOrFail_fspec N _

@[step] theorem uint.sub_spec {N : Usize} (a b : Array U64 N) (h : val b ≤ val a) :
    uint.sub a b ⦃ r => val r = val a - val b ⦄ := by
  have := val_lt a
  simp only [uint.sub, Lusterna.Uint.sub, h, ↓reduceIte, spec_ok]
  exact val_ofNat_of_lt (by omega)

@[step] theorem uint.sub_fspec {N : Usize} (a b : Array U64 N) :
    uint.sub a b ⦃ r => val b ≤ val a ∧ val r = val a - val b ⦄fail := by
  apply fspec_of_spec_pre _ (uint.sub_spec a b)
  intro r hr
  simp only [uint.sub, Lusterna.Uint.sub] at hr
  split at hr
  · assumption
  · simp at hr

@[step] theorem uint.mul_spec {N : Usize} (a b : Array U64 N) (h : val a * val b < modulus N) :
    uint.mul a b ⦃ r => val r = val a * val b ⦄ :=
  ofNatOrFail_spec N h

@[step] theorem uint.mul_fspec {N : Usize} (a b : Array U64 N) :
    uint.mul a b ⦃ r => val a * val b < modulus N ∧ val r = val a * val b ⦄fail :=
  ofNatOrFail_fspec N _

@[step] theorem uint.mul_u64_spec {N : Usize} (a : Array U64 N) (b : U64)
    (h : val a * b.val < modulus N) :
    uint.mul_u64 a b ⦃ r => val r = val a * b.val ⦄ :=
  ofNatOrFail_spec N h

@[step] theorem uint.mul_u64_fspec {N : Usize} (a : Array U64 N) (b : U64) :
    uint.mul_u64 a b ⦃ r => val a * b.val < modulus N ∧ val r = val a * b.val ⦄fail :=
  ofNatOrFail_fspec N _

@[step] theorem uint.div_mod_spec {N : Usize} (a b : Array U64 N) (h0 : val b ≠ 0) :
    uint.div_mod a b ⦃ r => val r.1 = val a / val b ∧ val r.2 = val a % val b ⦄ :=
  divMod_spec a b h0

@[step] theorem uint.div_mod_fspec {N : Usize} (a b : Array U64 N) :
    uint.div_mod a b ⦃ r => val b ≠ 0 ∧ val r.1 = val a / val b ∧ val r.2 = val a % val b ⦄fail :=
  fspec_of_spec_pre (fun _ h => ne_zero_of_divMod h) (divMod_spec a b)

@[step] theorem uint.div_spec {N : Usize} (a b : Array U64 N) (h0 : val b ≠ 0) :
    uint.div a b ⦃ r => val r = val a / val b ⦄ := by
  unfold uint.div
  exact spec_bind (divMod_spec a b h0) fun ⟨_, _⟩ h => by simpa using h.1

@[step] theorem uint.div_fspec {N : Usize} (a b : Array U64 N) :
    uint.div a b ⦃ r => val b ≠ 0 ∧ val r = val a / val b ⦄fail := by
  unfold uint.div
  exact fspec_bind (uint.div_mod_fspec a b) fun ⟨_, _⟩ h => (fspec_ok _).mpr ⟨h.1, h.2.1⟩

@[step] theorem uint.rem_spec {N : Usize} (a b : Array U64 N) (h0 : val b ≠ 0) :
    uint.rem a b ⦃ r => val r = val a % val b ⦄ := by
  unfold uint.rem
  exact spec_bind (divMod_spec a b h0) fun ⟨_, _⟩ h => by simpa using h.2

@[step] theorem uint.rem_fspec {N : Usize} (a b : Array U64 N) :
    uint.rem a b ⦃ r => val b ≠ 0 ∧ val r = val a % val b ⦄fail := by
  unfold uint.rem
  exact fspec_bind (uint.div_mod_fspec a b) fun ⟨_, _⟩ h => (fspec_ok _).mpr ⟨h.1, h.2.2⟩
