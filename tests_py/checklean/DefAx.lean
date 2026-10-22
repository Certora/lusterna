import Aeneas
open Aeneas.Std

/-! Fixture for `checkDefAxioms` — the TRANSLATE-time opaque-footprint disclosure. The one property
that matters here and nowhere else in the suite: the footprint is the TRANSITIVE axiom closure over a
def's body, so a target that reaches an opaque leaf only INDIRECTLY (through another def, via an
error/formatting path in the real case) still carries it. That transitive reach is exactly the
value-type/Display leak this check exists to surface before a full PROVE is spent on a doomed
translation. `verify.py`'s fixture run asserts the exact per-def footprint below. -/
namespace Probe.DefAx

/-- Mimics an Aeneas-emitted opaque leaf (`axiom fixed.FixedU128.from_bits …`). -/
axiom Op : Nat → Nat

/-- A cleanly modelled value op: EMPTY opaque footprint (the control). -/
def cleanDef (x : Nat) : Nat := x + 1

/-- Reaches the opaque original DIRECTLY (the delegated-`Display` leak). -/
noncomputable def leakyDirect (x : Nat) : Nat := Op x + 1

/-- Reaches it only TRANSITIVELY (through `leakyDirect`) — the closure must still find `Op`. -/
noncomputable def leakyIndirect (x : Nat) : Nat := leakyDirect x + cleanDef x

end Probe.DefAx
