import Aeneas
import Probe

-- `⦃ ⦄` is `scoped syntax` in `namespace Aeneas`, so `open Aeneas` is required for it to parse;
-- `ok` lives in the `Aeneas.Std.Result` namespace.
open Aeneas Aeneas.Std Aeneas.Std.Result

namespace Probe.Spec.Fixture

-- ══ ordinary execution shapes — controls that must stay clean ══════════════════

-- FLAGGED: x never reaches anywhere else in the statement.
theorem bad1_output_unreached (x r : Nat) (h : Probe.f x = ok r) (hr : r > 0) : True := by sorry

-- clean: x reaches the conclusion, so the theorem is genuinely about what f does with x.
theorem good1_input_reaches_conclusion (x r : Nat) (h : Probe.f x = ok r) : r = x + 1 := by sorry

-- clean: injectivity. x, y both reach the conclusion. No special-casing needed — the pattern
-- simply never matches, unlike rule 1's old "any hypothesis mentioning an output" heuristic,
-- which flagged this family.
theorem injectivity (x y a b : Nat) (h1 : Probe.f x = ok a) (h2 : Probe.f y = ok b) (hab : a = b) :
    x = y := by sorry

-- clean: chained calls. r (h1's output) feeds h2 as an input; that's expected, not "unreached".
theorem chained (x r s : Nat) (h1 : Probe.f x = ok r) (h2 : Probe.g r = ok s) :
    s = x + 3 := by sorry

-- clean: a triple has no free-standing output fvar to check reachability of.
theorem good_triple (x : Nat) : Probe.f x ⦃ r => r > 0 ⦄ := by sorry

-- clean: x reaches elsewhere via an ordinary input precondition, regardless of binder order.
theorem reordered_binders (x : Nat) (hpre : x < 100) (r : Nat) (h : Probe.f x = ok r) :
    r < 101 := by sorry

-- clean: a genuine downstream-overflow guard, where x DOES reach the conclusion.
theorem downstream_guard (x r : Nat) (h : Probe.f x = ok r) (hs : r < 100) :
    Probe.g r = ok (x + r + 2) := by sorry

-- FLAGGED, both calls: the deposit_does_not_decrease_share_price shape in miniature. Two
-- independent calls; their outputs are related to EACH OTHER via `hrel`, while x and y (what was
-- actually fed into f and g) never surface again. Swap f/g for any other functions returning some
-- r/s and the "proof" is unaffected.
theorem two_independent_calls (x y r s : Nat) (h1 : Probe.f x = ok r) (h2 : Probe.g y = ok s)
    (hrel : s = r + 1) : s = r + 1 := by sorry

-- FLAGGED: x never reaches the conclusion (which is purely a fact about r, k — Nat arithmetic
-- true for ANY r ≥ 0). The subject's own equation written into the conclusion is a legitimate
-- STYLE (no top-level pin needed), but THIS particular statement happens not to depend on f at
-- all — a real, if narrow, finding.
theorem subject_in_conclusion_but_trivial (x k : Nat) (hk : k ≤ 3) :
    ∀ r, Probe.f x = ok r → k ≤ r + 3 := by sorry

end Probe.Spec.Fixture
