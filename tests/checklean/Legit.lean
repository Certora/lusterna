import Aeneas

open Aeneas Aeneas.Std Aeneas.Std.Result

/-!
Fixture for `assumption_legitimacy` (`checkAssumptionLegitimacy`). The trusted base may hold facts
ONLY about the substrate, never a property of a TARGET — an axiom whose type references a target could
BE a goal, and admitting it would relax what must be proved. The check walks each axiom's ELABORATED
type, so a target reached through notation/coercion/`abbrev` is a real `.const` occurrence and is
caught even when the surface spelling does not contain the target's name.
-/
namespace Probe.Legit

/-- a TARGET function under verification (a crate `def`, matched by the pattern `deposit`). -/
def deposit (x : Nat) : Result Nat := ok (x + 1)

/-- a SUBSTRATE primitive: a crate `def`, but NOT among the target patterns, so an assumption about
it is admissible (the substrate is exactly what the trusted base may talk about). -/
def limbMul (a b : Nat) : Nat := a * b

/-- LEGIT — mentions only the substrate `limbMul`, never a target. Must NOT be flagged. -/
axiom limbMul_spec (a b : Nat) : limbMul a b = a * b

/-- ILLEGIT — its type references the target `deposit`, so it could relax that target's goal. Must be
flagged (`assumption_legitimacy`, target `deposit`). -/
axiom deposit_monotone (x : Nat) : ∃ r, deposit x = ok r ∧ x ≤ r

end Probe.Legit
