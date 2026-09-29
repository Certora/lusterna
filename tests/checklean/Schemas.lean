import Aeneas
import Probe
import Probe.LusternaSchemas
open Aeneas Aeneas.Std Aeneas.Std.Result

/-! Fixture for `schema_conformance` under the TRIPLE checked-schema. A checked property is an Aeneas
WP triple over a declared target, `f args ⦃ r => post r ⦄`, whose postcondition MENTIONS the produced
value, is a PURE proposition (no nested `Aeneas.Std.Result` measurement), and genuinely CONSTRAINS it
(not a self-assuming tautology). The triple gives single-execution / totality / output-freshness for
free, so only the postcondition is vetted. Targets: `Probe.f` (`Probe.g`/`Probe.k` are NOT targets in
the schema config, used to exercise `triple_not_over_target` and the anchor-coverage split). -/
namespace Probe.Schemas

def k (x : Nat) : Result Nat := ok x

-- Vacuity carriers: a self-assuming implication behind a predicate (at depth), and a Prop structure.
def SelfImp (r x : Nat) : Prop := r > x → r > x
def W2 (r x : Nat) : Prop := SelfImp r x
def W3 (r x : Nat) : Prop := W2 r x
structure SelfStruct (r x : Nat) : Prop where
  h : r > x → r > x

-- ── CONFORMING checked properties — must draw NOTHING ──
@[lusterna]
theorem s1_conforms (x : Nat) : Probe.f x ⦃ r => r > x ⦄ := by sorry

/-- A genuine conditional postcondition (`A → B`, `B ∉ conjuncts A`) is NOT vacuous — the control that
`postConstrains?` does not over-flag every implication. -/
@[lusterna]
theorem s2_conditional_clean (x : Nat) : Probe.f x ⦃ r => r > 0 → r > x ⦄ := by sorry

/-- A supporting lemma opts out of the checked shape; the reason is validated at elaboration. -/
@[lusterna_lemma "relational: a pure algebraic fact, not a single-execution property"]
theorem s3_lemma (x y : Nat) : x = y → y = x := by sorry

-- ── ONE BROKEN RULE EACH ──
/-- `not_a_target_triple` — the equational form is no longer a checked shape. -/
@[lusterna]
theorem s5_not_a_triple (x r : Nat) (h : Probe.f x = ok r) : r > x := by sorry

/-- `triple_not_over_target` — a triple, but its computation runs `k`, which the schema config does
NOT list as a target. -/
@[lusterna]
theorem s6_triple_not_over_target (x : Nat) : Probe.Schemas.k x ⦃ r => r > x ⦄ := by sorry

/-- `conclusion_ignores_output` — the postcondition never mentions the produced value `r`. -/
@[lusterna]
theorem s7_ignores_output (x : Nat) : Probe.f x ⦃ r => x ≥ x ⦄ := by sorry

/-- `claim_not_failsafe` — the postcondition runs a fallible measurement (`Probe.g r`), which could
fail OPEN. (This is the old failure-strictness family, folded into the postcondition purity check.) -/
@[lusterna]
theorem s8_post_measurement (x : Nat) : Probe.f x ⦃ r => Probe.g r = ok r ⦄ := by sorry

/-- `vacuous_claim` — a self-assuming implication `Q → Q`. -/
@[lusterna]
theorem s9_vacuous_direct (x : Nat) : Probe.f x ⦃ r => r > x → r > x ⦄ := by sorry

/-- `vacuous_claim` — the same, hidden behind named predicates at depth (`W3 → W2 → SelfImp`). -/
@[lusterna]
theorem s10_vacuous_predicate (x : Nat) : Probe.f x ⦃ r => W3 r x ⦄ := by sorry

/-- `vacuous_claim` — the same, behind a connective (`∧ True`). -/
@[lusterna]
theorem s11_vacuous_conj (x : Nat) : Probe.f x ⦃ r => (r > x → r > x) ∧ True ⦄ := by sorry

/-- `vacuous_claim` — the antecedent hidden in a Prop-valued STRUCTURE field. -/
@[lusterna]
theorem s12_vacuous_structure (x : Nat) : Probe.f x ⦃ r => SelfStruct r x ⦄ := by sorry

/-- `no_schema_declared` — a spec theorem must declare a family. -/
theorem s13_unannotated (x : Nat) : Probe.f x ⦃ r => r > x ⦄ := by sorry

/-- `multiple_schemas_declared` — a theorem is checked or exempt, not both. -/
@[lusterna, lusterna_lemma "cannot be both"]
theorem s14_double (x : Nat) : Probe.f x ⦃ r => r > x ⦄ := by sorry

-- ── ANCHOR-COVERAGE fixtures ──
/-- An equational bridge to `g` written as an exempt lemma: it MEASURES `g` (so `anchor_bridge`
reports `g` referenced) but is NOT a checked triple (so `anchor_checked` reports `g` un-covered).
This is the split prong 3 keys on — the checked family may not be quietly emptied. -/
@[lusterna_lemma "equational bridge to a getter, not a checked property"]
theorem g_lemma_bridge (x r : Nat) (h : Probe.g x = ok r) : r ≥ x := by sorry

end Probe.Schemas
