module
public import LusternaSpec.FSpec
import all Aeneas.Std.Scalar.Ops.Add
import all Aeneas.Std.Scalar.Ops.Neg

/-!
# `fspec` theorems for the arithmetic of scalars

Each theorem is the `fspec` version of a `spec` theorem of Aeneas. The hypotheses of the Aeneas
theorem are in the postcondition, in front of its postcondition. Each section names the Aeneas
file that its theorems belong in.
-/

@[expose] public section

namespace Lusterna.Spec

open Aeneas Aeneas.Std Result WP

/-- If `m` returns only when `H` holds, a triple of `m` that assumes `H` gives an `fspec` of `m`
with `H` in the postcondition. -/
theorem fspec_of_spec_pre {α} {m : Result α} {H : Prop} {P : Post α}
    (hH : ∀ z, m = ok z → H) (hs : H → spec m P) : fspec m (fun z => H ∧ P z) := by
  rw [fspec_iff]
  intro z hz
  have h := hH z hz
  have hp := hs h
  rw [hz] at hp
  exact ⟨h, by simpa using hp⟩

end Lusterna.Spec

namespace Aeneas.Std

open Result Error Arith ScalarElab WP
open Lusterna.Spec

/-!
# Addition

Upstream: `Aeneas/Std/Scalar/Ops/Add.lean`.
-/

/-- If `x + y` returns `z`, the sum is at most `UScalar.max ty`, and `z` is the sum. -/
@[step]
theorem UScalar.add_fspec {ty} {x y : UScalar ty} :
    x + y ⦃ z => ↑x + ↑y ≤ UScalar.max ty ∧ (↑z : Nat) = ↑x + ↑y ⦄fail := by
  apply fspec_of_spec_pre _ UScalar.add_spec
  intro z hz
  have h := @add_equiv ty x y
  rw [hz] at h
  simp at h
  simp [max]
  omega

/-- If `x + y` returns `z`, the sum is between `IScalar.min ty` and `IScalar.max ty`, and `z` is
the sum. -/
@[step]
theorem IScalar.add_fspec {ty} {x y : IScalar ty} :
    x + y ⦃ z => (IScalar.min ty ≤ ↑x + ↑y ∧ ↑x + ↑y ≤ IScalar.max ty) ∧
      (↑z : Int) = ↑x + ↑y ⦄fail := by
  apply fspec_of_spec_pre _ (fun h => IScalar.add_spec h.1 h.2)
  intro z hz
  have h := @add_equiv ty x y
  rw [hz] at h
  simp [inBounds] at h
  simp [min, max]
  omega

uscalar /-- `UScalar.add_fspec` for one unsigned type. -/
@[step] theorem «%S».add_fspec {x y : «%S»} :
    x + y ⦃ z => x.val + y.val ≤ «%S».max ∧ (↑z : Nat) = ↑x + ↑y ⦄fail :=
  fspec_mono UScalar.add_fspec fun _ h => ⟨by scalar_tac, h.2⟩

iscalar /-- `IScalar.add_fspec` for one signed type. -/
@[step] theorem «%S».add_fspec {x y : «%S»} :
    x + y ⦃ z => («%S».min ≤ ↑x + ↑y ∧ ↑x + ↑y ≤ «%S».max) ∧ (↑z : Int) = ↑x + ↑y ⦄fail :=
  fspec_mono IScalar.add_fspec fun _ h => ⟨by scalar_tac, h.2⟩

/-- `UScalar.add_fspec` for the addition through a shared reference. -/
@[step]
theorem SharedUScalar.Insts.CoreOpsArithAddUScalarUScalar.add_fspec {ty} {x y : UScalar ty} :
    SharedUScalar.Insts.CoreOpsArithAddUScalarUScalar.add x y
      ⦃ z => ↑x + ↑y ≤ UScalar.max ty ∧ (↑z : Nat) = ↑x + ↑y ⦄fail :=
  UScalar.add_fspec

uscalar /-- `UScalar.add_fspec` for the addition through a shared reference, for one unsigned
type. -/
@[step] theorem «Shared'S».Insts.«CoreOpsArithAdd'S'S».add_fspec {x y : «%S»} :
    «Shared'S».Insts.«CoreOpsArithAdd'S'S».add x y
      ⦃ z => x.val + y.val ≤ «%S».max ∧ (↑z : Nat) = ↑x + ↑y ⦄fail :=
  «%S».add_fspec

/-!
# Subtraction

Upstream: `Aeneas/Std/Scalar/Ops/Sub.lean`.
-/

/-- If `x - y` returns `z`, then `y ≤ x`, and `z` is the difference. -/
@[step]
theorem UScalar.sub_fspec {ty} {x y : UScalar ty} :
    x - y ⦃ z => y.val ≤ x.val ∧ z.val = x.val - y.val ⦄fail := by
  apply fspec_mono (fspec_of_spec_pre _ UScalar.sub_spec) fun _ h => ⟨h.1, h.2.1⟩
  intro z hz
  have h := @sub_equiv ty x y
  rw [hz] at h
  simp at h
  omega

/-- If `x - y` returns `z`, the difference is between `IScalar.min ty` and `IScalar.max ty`, and
`z` is the difference. -/
@[step]
theorem IScalar.sub_fspec {ty} {x y : IScalar ty} :
    x - y ⦃ z => (IScalar.min ty ≤ ↑x - ↑y ∧ ↑x - ↑y ≤ IScalar.max ty) ∧
      (↑z : Int) = ↑x - ↑y ⦄fail := by
  apply fspec_of_spec_pre _ (fun h => IScalar.sub_spec h.1 h.2)
  intro z hz
  have h := @sub_equiv ty x y
  rw [hz] at h
  simp [inBounds] at h
  simp [min, max]
  omega

uscalar /-- `UScalar.sub_fspec` for one unsigned type. -/
@[step] theorem «%S».sub_fspec {x y : «%S»} :
    x - y ⦃ z => y.val ≤ x.val ∧ z.val = x.val - y.val ⦄fail :=
  UScalar.sub_fspec

iscalar /-- `IScalar.sub_fspec` for one signed type. -/
@[step] theorem «%S».sub_fspec {x y : «%S»} :
    x - y ⦃ z => («%S».min ≤ ↑x - ↑y ∧ ↑x - ↑y ≤ «%S».max) ∧ (↑z : Int) = ↑x - ↑y ⦄fail :=
  fspec_mono IScalar.sub_fspec fun _ h => ⟨by scalar_tac, h.2⟩

/-!
# Multiplication

Upstream: `Aeneas/Std/Scalar/Ops/Mul.lean`.
-/

/-- If `x * y` returns `z`, the product is at most `UScalar.max ty`, and `z` is the product. -/
theorem UScalar.mul_fspec {ty} {x y : UScalar ty} :
    x * y ⦃ z => ↑x * ↑y ≤ UScalar.max ty ∧ (↑z : Nat) = ↑x * ↑y ⦄fail := by
  apply fspec_of_spec_pre _ UScalar.mul_spec
  intro z hz
  have h := @mul_equiv ty x y
  rw [show x.mul y = ok z from hz] at h
  simp at h
  exact h.1

/-- If `x * y` returns `z`, the product is between `IScalar.min ty` and `IScalar.max ty`, and `z`
is the product. -/
theorem IScalar.mul_fspec {ty} {x y : IScalar ty} :
    x * y ⦃ z => (IScalar.min ty ≤ ↑x * ↑y ∧ ↑x * ↑y ≤ IScalar.max ty) ∧
      (↑z : Int) = ↑x * ↑y ⦄fail := by
  apply fspec_of_spec_pre _ (fun h => IScalar.mul_spec h.1 h.2)
  intro z hz
  have h := @mul_equiv ty x y
  rw [show x.mul y = ok z from hz] at h
  simp at h
  exact ⟨h.1, h.2.1⟩

uscalar /-- `UScalar.mul_fspec` for one unsigned type. -/
@[step] theorem «%S».mul_fspec {x y : «%S»} :
    x * y ⦃ z => x.val * y.val ≤ «%S».max ∧ (↑z : Nat) = ↑x * ↑y ⦄fail :=
  fspec_mono UScalar.mul_fspec fun _ h => ⟨by scalar_tac, h.2⟩

iscalar /-- `IScalar.mul_fspec` for one signed type. -/
@[step] theorem «%S».mul_fspec {x y : «%S»} :
    x * y ⦃ z => («%S».min ≤ ↑x * ↑y ∧ ↑x * ↑y ≤ «%S».max) ∧ (↑z : Int) = ↑x * ↑y ⦄fail :=
  fspec_mono IScalar.mul_fspec fun _ h => ⟨by scalar_tac, h.2⟩

/-!
# Division

Upstream: `Aeneas/Std/Scalar/Ops/Div.lean`.
-/

/-- If `x / y` returns `z`, then `y` is not zero, and `z` is the quotient. -/
theorem UScalar.div_fspec {ty} (x : UScalar ty) {y : UScalar ty} :
    x / y ⦃ z => y.val ≠ 0 ∧ (↑z : Nat) = ↑x / ↑y ⦄fail := by
  apply fspec_of_spec_pre _ fun h => ?_
  · intro z hz hy
    have : y.bv = 0 := by apply BitVec.eq_of_toNat_eq; simpa using hy
    simp [HDiv.hDiv, UScalar.div, this] at hz
  · obtain ⟨z, hz, hpost⟩ := UScalar.div_spec x h
    simpa only [hz, spec_ok] using hpost

/-- If `x / y` returns `z`, then `y` is not zero, the division does not overflow, and `z` is the
quotient, rounded towards zero. -/
theorem IScalar.div_fspec {ty} {x y : IScalar ty} :
    x / y ⦃ z => (y.val ≠ 0 ∧ ¬ (x.val = IScalar.min ty ∧ y.val = -1)) ∧
      (↑z : Int) = Int.tdiv ↑x ↑y ⦄fail := by
  apply fspec_of_spec_pre _ fun h => ?_
  · intro z hz
    simp only [HDiv.hDiv, IScalar.div] at hz
    split at hz
    · split at hz
      · simp_all
      · simp at hz
    · simp at hz
  · obtain ⟨z, hz, hpost⟩ := IScalar.div_spec h.1 h.2
    simpa only [hz, spec_ok] using hpost

uscalar /-- `UScalar.div_fspec` for one unsigned type. -/
@[step] theorem «%S».div_fspec (x : «%S») {y : «%S»} :
    x / y ⦃ z => y.val ≠ 0 ∧ (↑z : Nat) = ↑x / ↑y ⦄fail :=
  UScalar.div_fspec x

iscalar /-- `IScalar.div_fspec` for one signed type. -/
@[step] theorem «%S».div_fspec {x y : «%S»} :
    x / y ⦃ z => (y.val ≠ 0 ∧ ¬ (x.val = «%S».min ∧ y.val = -1)) ∧
      (↑z : Int) = Int.tdiv ↑x ↑y ⦄fail :=
  fspec_mono IScalar.div_fspec fun _ h => ⟨⟨h.1.1, by scalar_tac⟩, h.2⟩

/-!
# Remainder

Upstream: `Aeneas/Std/Scalar/Ops/Rem.lean`.
-/

/-- If `x % y` returns `z`, then `y` is not zero, and `z` is the remainder. -/
theorem UScalar.rem_fspec {ty} (x : UScalar ty) {y : UScalar ty} :
    x % y ⦃ z => y.val ≠ 0 ∧ (↑z : Nat) = ↑x % ↑y ⦄fail := by
  apply fspec_of_spec_pre _ (UScalar.rem_spec x)
  intro z hz hy
  simp [HMod.hMod, UScalar.rem, hy] at hz

/-- If `x % y` returns `z`, then `y` is not zero, and `z` is the remainder of the division that
rounds towards zero. -/
theorem IScalar.rem_fspec {ty} (x : IScalar ty) {y : IScalar ty} :
    x % y ⦃ z => y.val ≠ 0 ∧ (↑z : Int) = Int.tmod ↑x ↑y ⦄fail := by
  apply fspec_of_spec_pre _ (IScalar.rem_spec x)
  intro z hz hy
  simp [HMod.hMod, IScalar.rem, hy] at hz

uscalar /-- `UScalar.rem_fspec` for one unsigned type. -/
@[step] theorem «%S».rem_fspec (x : «%S») {y : «%S»} :
    x % y ⦃ z => y.val ≠ 0 ∧ (↑z : Nat) = ↑x % ↑y ⦄fail :=
  UScalar.rem_fspec x

iscalar /-- `IScalar.rem_fspec` for one signed type. -/
@[step] theorem «%S».rem_fspec (x : «%S») {y : «%S»} :
    x % y ⦃ z => y.val ≠ 0 ∧ (↑z : Int) = Int.tmod ↑x ↑y ⦄fail :=
  IScalar.rem_fspec x

/-!
# Negation

Upstream: `Aeneas/Std/Scalar/Ops/Neg.lean`.
-/

/-- If `IScalar.neg x` returns `r`, then `x` is not `IScalar.min ty`, and `r` is `-x`. -/
@[step]
theorem IScalar.neg_fstep {ty} (x : IScalar ty) :
    IScalar.neg x ⦃ r => x ≠ IScalar.min ty ∧ r = -x.val ⦄fail := by
  apply fspec_of_spec_pre _ (IScalar.neg_step x)
  intro z hz hx
  have h := IScalar.tryMk_eq ty (-x.val)
  rw [show IScalar.tryMk ty (-x.val) = ok z from hz] at h
  simp [inBounds] at h
  simp [IScalar.min] at hx
  omega

/-- `IScalar.neg_fstep` for the notation `-. x`. -/
@[step]
theorem HNeg.hNeg.fstep {ty} (x : IScalar ty) :
    HNeg.hNeg x ⦃ r => x ≠ IScalar.min ty ∧ r = -x.val ⦄fail :=
  IScalar.neg_fstep x

end Aeneas.Std
