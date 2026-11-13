import Aeneas
import LusternaSpec.Attr
import LusternaSpec.FSpec

/-!
# Fixture

The theorems that `LusternaSpecTest` runs `Lusterna.Spec.check` on.
-/

open Aeneas Aeneas.Std

namespace LusternaSpecTest

def f (x : U32) : Result U32 := x + 1#u32

/-- A triple with a postcondition about the result. -/
@[lusterna 1 "f increments"]
theorem f_increments (x : U32) (h : x.val < 100) : f x ⦃ r => r.val = x.val + 1 ⦄ := by
  sorry

/-- Not a triple. -/
@[lusterna 2 "f is f"]
theorem f_eq (x : U32) : f x = f x := by
  sorry

/-- A postcondition that ignores the result and holds for every result. -/
@[lusterna 3 "f returns"]
theorem f_returns (x : U32) : f x ⦃ _ => True ⦄ := by
  sorry

/-- A postcondition that runs `f`. -/
@[lusterna 4 "f is a fixpoint"]
theorem f_fixpoint (x : U32) : f x ⦃ r => f r = .ok r ⦄ := by
  sorry

open Lusterna.Spec in
/-- An `fspec` triple with a postcondition about the result. -/
@[lusterna 5 "f increments if it returns"]
theorem f_increments_fail (x : U32) : f x ⦃ r => r.val = x.val + 1 ⦄fail := by
  sorry

/-- A proof that uses a theorem whose proof is `sorry`. -/
theorem f_eq_by_lemma (x : U32) : f x = f x := f_eq x

/-- An unlabelled theorem with a proof. -/
theorem two : 1 + 1 = 2 := rfl

end LusternaSpecTest
