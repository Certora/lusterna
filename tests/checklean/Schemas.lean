import Aeneas
import Probe
import Probe.Invariant
import Probe.LusternaSchemas

/-! Fixture for `schema_conformance`.

Each theorem DECLARES a family (`@[lusterna]` checked, or `@[lusterna_lemma]` exempt) and then either
conforms or breaks exactly one rule, so a finding's `rule` field is what this pins — not merely that
something fired. Reuses `Probe.Inv`'s state, measurements and target rather than redefining them.

TWO DEFENSIVE PATHS ARE DELIBERATELY NOT EXERCISED HERE, because both are rejected at
ELABORATION time and a fixture that fails to build cannot be in the suite:
  • `@[lusterna_lemma ""]` — the attribute's own `getParam` throws on an all-whitespace reason.
  • a `private` annotated theorem — reported by the `_private` guard in `checkSchemaConformance`,
    but its mangled name (`_private.…`) is awkward to reference portably from a driver.
-/

open Aeneas Aeneas.Std Aeneas.Std.Result

namespace Probe.Schemas
open Probe.Inv

/-! ── the Pre/Post pair a conforming Hoare triple is written against ─────────────────────────── -/

def TransferPre (amt : U64) (s : St) : Result Bool := do
  let t ← total_supply s
  ok (t.val + amt.val < 1000)

def TransferPost (amt : U64) (s s' : St) : Result Bool := do
  let t ← total_supply s
  let t' ← total_supply s'
  ok (t'.val == amt.val && t.val ≤ 1000)

/-- Fail-open: rejected as `predicate_not_failure_strict` by `claimFailSafe?`'s `certifiedStrict`. -/
def TransferPostOpen (amt : U64) (s s' : St) : Result Bool :=
  ok (! ok? (total_supply s') || amt.val == 0)

/-- Mentions no post-state at all, so it claims nothing about the call — rule 6. -/
def PostIgnoresOutput (amt : U64) (s : St) : Result Bool := do
  let t ← total_supply s
  ok (t.val == amt.val)

/-! ── the REAL Aeneas output shape: `Result (core.result.Result Eff Err × St)` ─────────────────

Every other theorem here runs `transfer`, whose outputs are `(y : Unit, s' : St)` — one exempt and
one plain variable. That does not exercise the shape production code actually has: TWO informative
outputs, one of them behind the `core.result.Result.Ok` neutral wrapper. Rule 6 is the check most
able to be silently vacuous, so it is tested on a subset (`Post` mentions `vfinal` but not `eff`)
rather than only on an all-or-nothing miss. -/

inductive Err where | boom
structure Eff where
  amount : U64

def transferEff (amt : U64) (s : St) : Result (core.result.Result Eff Err × St) :=
  ok (.Ok ⟨amt⟩, { s with total := amt })

def EffPost (amt : U64) (s : St) (e : Eff) (s' : St) : Result Bool := do
  let t' ← total_supply s'
  ok (t'.val == amt.val && e.amount.val == amt.val)

/-- Mentions the post-state but NOT `eff` — a subset miss, which an all-or-nothing rule 6 would let
through. -/
def EffPostIgnoresEff (amt : U64) (s s' : St) : Result Bool := do
  let t' ← total_supply s'
  ok (t'.val == amt.val)

/-! ── CONFORMING: must draw nothing ──────────────────────────────────────────────────────────── -/

@[lusterna]
theorem s1_conforms (amt : U64) (s s' : St) (y : Unit)
    (hpre  : TransferPre amt s = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- No precondition at all is fine: the fail-safe rule constrains the propositions that ARE there. -/
@[lusterna]
theorem s2_no_precondition (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- A preservation property — the same claim on the pre- and post-state — is the SAME checked shape,
no separate "invariant" label. -/
@[lusterna]
theorem s3_preservation (amt : U64) (s s' : St) (y : Unit)
    (hinv  : InvFlat s = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    InvFlat s' = ok true := by sorry

@[lusterna_lemma "pure arithmetic helper; no target execution to speak of"]
theorem s4_lemma (n : Nat) : n + 0 = n := by sorry

/-! ── THE INTENDED LOOSENING: shapes the old invariant/hoare split rejected, now CLEAN ─────────

Each of these used to draw a finding purely because of the invariant-vs-hoare ceremony; under the one
checked shape they conform, and pinning them CLEAN is the regression guard that the ceremony is gone. -/

/-- Was `hypothesis_not_a_precondition`. A pure-`Prop` bound on a ROOT input is a legitimate
precondition — it names no measurement, so it cannot fail open. -/
@[lusterna]
theorem s8_pure_precondition_clean (amt : U64) (s s' : St) (y : Unit)
    (hb    : amt.val < 100)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- Was `conclusion_not_a_postcondition`. A raw equation about the output IS the readable plain-`Prop`
claim the redesign is for — no `Post … = ok true` wrapper required. -/
@[lusterna]
theorem s9_plain_prop_conclusion_clean (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    s'.total = amt := by sorry

/-- Was `postcondition_ignores_output` on a subset. Rule 5 is now "at least one output", so a
property that speaks of `s'` and ignores `eff` conforms — a preservation need not pin every output. -/
@[lusterna]
theorem s17_mentions_one_of_two_clean (amt : U64) (s s' : St) (eff : Eff)
    (hexec : transferEff amt s = ok (.Ok eff, s')) :
    EffPostIgnoresEff amt s s' = ok true := by sorry

/-- Was `extraneous_hypothesis` — the `deposit_liquidity` shape from a real klend spec, the exact case
the old split forced from `invariant` to `hoare`. A preservation carrying one extra pure precondition
is one checked property; nothing to reclassify. THIS conforming is the whole point of the merge. -/
@[lusterna]
theorem g4_preservation_with_side_condition_clean (amt : U64) (s s' : St) (y : Unit)
    (hinv  : InvFlat s = ok true)
    (hb    : amt.val < 100)
    (hexec : transfer amt s = ok (y, s')) :
    InvFlat s' = ok true := by sorry

/-! ── NON-CONFORMING: one broken rule each ───────────────────────────────────────────────────── -/

/-- `execution_not_unique` — two target executions. This rule is what makes `assumed_postcondition`'s
taint fixpoint unnecessary here, so it is the load-bearing one. -/
@[lusterna]
theorem s5_two_executions (amt amt2 : U64) (s s1 s2 : St) (y y2 : Unit)
    (hexec  : transfer amt s = ok (y, s1))
    (hexec2 : transfer amt2 s1 = ok (y2, s2)) :
    TransferPost amt s s2 = ok true := by sorry

/-- `execution_output_not_fresh` — the post-state IS the pre-state. -/
@[lusterna]
theorem s6_pinned_output (amt : U64) (s : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s)) :
    TransferPost amt s s = ok true := by sorry

/-- Provenance, not shape: a precondition that speaks about the POST-state is FAIL-SAFE (it conforms),
so it passes conformance and is then caught by `assumed_postcondition` — the finding is that check's,
not `schema_conformance`'s. -/
@[lusterna]
theorem s7_pre_mentions_output (amt : U64) (s s' : St) (y : Unit)
    (hpre  : TransferPre amt s' = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- `predicate_not_failure_strict` — the conclusion can be satisfied by a failing measurement. -/
@[lusterna]
theorem s10_post_not_strict (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPostOpen amt s s' = ok true := by sorry

/-- `conclusion_ignores_output` — conforms to every other rule while saying nothing about what
`transfer` did (`PostIgnoresOutput` speaks only of the pre-state `s`). `y : Unit` is NOT demanded: a
single-constructor-no-field type carries no claim, so only `s'` counts, and it is absent. -/
@[lusterna]
theorem s11_conclusion_ignores_output (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    PostIgnoresOutput amt s = ok true := by sorry

/-- `no_schema_declared` — the ratchet. Unannotated must not be the cheap way past the check. -/
theorem s14_unannotated (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- `multiple_schemas_declared` — a theorem cannot be both a checked property and an exempt lemma;
being checked against the laxer of two is not a result. -/
@[lusterna, lusterna_lemma "cannot be both"]
theorem s15_double_annotated (amt : U64) (s s' : St) (y : Unit)
    (hinv  : InvFlat s = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    InvFlat s' = ok true := by sorry

/-- CONFORMS with the real Aeneas shape: `outputLeaves` must descend through the neutral
`core.result.Result.Ok` wrapper to find `eff`, and `Post` mentions it. -/
@[lusterna]
theorem s16_wrapped_outputs_conform (amt : U64) (s s' : St) (eff : Eff)
    (hexec : transferEff amt s = ok (.Ok eff, s')) :
    EffPost amt s eff s' = ok true := by sorry

/-! ── THE GATE's own composition: precedence and lemma scoping ────────────────────────────────── -/

/-- PRECEDENCE. Shape wrong (two executions) AND the conclusion is fail-open. The gate must report the
shape finding ALONE — a strictness finding on a theorem whose shape is already wrong buries the one
message FORMALISE can act on. -/
@[lusterna]
theorem g1_precedence_shape_first (amt amt2 : U64) (s s1 s2 : St) (y y2 : Unit)
    (hexec  : transfer amt s = ok (y, s1))
    (hexec2 : transfer amt2 s1 = ok (y2, s2)) :
    TransferPostOpen amt s s2 = ok true := by sorry

/-- LEMMA SCOPING. This is injectivity — a relational, two-run property. It is
`assumed_postcondition`'s documented unavoidable false positive, and it is why the shape rules were
once advisory. Declared a lemma, the gate must NOT block it. -/
@[lusterna_lemma "relational: two executions by construction, not a single-execution property"]
theorem g2_injectivity_is_lemma (amt amt2 : U64) (s s1 s2 : St) (y y2 : Unit)
    (h1 : transfer amt s = ok (y, s1)) (h2 : transfer amt2 s = ok (y2, s2))
    (heq : s1 = s2) : amt = amt2 := by sorry

/-- LEMMA SCOPING, second shape: a bound on an intermediate — the other documented false positive.
Out of scope by declaration, so it must not block either. -/
@[lusterna_lemma "overflow guard on an intermediate, stated where it is needed"]
theorem g3_intermediate_bound_is_lemma (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) (hb : s'.total.val < 100) :
    s'.total.val < 1000 := by sorry

/-- The ANCHOR BRIDGE fixture (for `checkAnchorReference`): a theorem whose TYPE directly references
the real measurement `Probe.Inv.total_supply`, so the anchor `total_supply` reads as referenced while
an anchor no theorem names does not. This is the bridge a reconstructed projection would carry. -/
@[lusterna_lemma "bridges the projection to the real total_supply getter"]
theorem total_supply_reads (s : St) (t : U64) (h : total_supply s = ok t) : t = s.total := by sorry

end Probe.Schemas
