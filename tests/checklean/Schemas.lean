import Aeneas
import Probe
import Probe.Invariant
import Probe.LusternaSchemas

/-! Fixture for `schema_conformance`.

Each theorem DECLARES a schema and then either conforms or breaks exactly one rule, so a finding's
`rule` field is what this pins — not merely that something fired. Reuses `Probe.Inv`'s state,
measurements and target rather than redefining them.

TWO DEFENSIVE PATHS ARE DELIBERATELY NOT EXERCISED HERE, because both are rejected at
ELABORATION time and a fixture that fails to build cannot be in the suite:
  • `@[lusterna_freeform ""]` — the attribute's own `getParam` throws on an all-whitespace reason.
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

/-- Fail-open: rejected by rule 5, the same `certifiedStrict` fragment `invariant_not_strict` uses. -/
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

@[lusterna_hoare]
theorem s1_hoare_conforms (amt : U64) (s s' : St) (y : Unit)
    (hpre  : TransferPre amt s = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- No precondition at all is fine: rule 3 constrains the propositions that ARE there. -/
@[lusterna_hoare]
theorem s2_hoare_no_precondition (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

@[lusterna_invariant]
theorem s3_invariant_conforms (amt : U64) (s s' : St) (y : Unit)
    (hinv  : InvFlat s = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    InvFlat s' = ok true := by sorry

@[lusterna_freeform "pure arithmetic helper; no target execution to speak of"]
theorem s4_freeform (n : Nat) : n + 0 = n := by sorry

/-! ── NON-CONFORMING: one broken rule each ───────────────────────────────────────────────────── -/

/-- `execution_not_unique` — two target executions. Rule 1 is what makes `assumed_postcondition`'s taint fixpoint
unnecessary here, so it is the load-bearing one. -/
@[lusterna_hoare]
theorem s5_two_executions (amt amt2 : U64) (s s1 s2 : St) (y y2 : Unit)
    (hexec  : transfer amt s = ok (y, s1))
    (hexec2 : transfer amt2 s1 = ok (y2, s2)) :
    TransferPost amt s s2 = ok true := by sorry

/-- `execution_output_not_fresh` — the post-state IS the pre-state. -/
@[lusterna_hoare]
theorem s6_pinned_output (amt : U64) (s : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s)) :
    TransferPost amt s s = ok true := by sorry

/-- `precondition_mentions_output` — the precondition speaks about the POST-state. The finding must
name *which* argument, or "move it into `Pre`" is not actionable. -/
@[lusterna_hoare]
theorem s7_pre_mentions_output (amt : U64) (s s' : St) (y : Unit)
    (hpre  : TransferPre amt s' = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- `hypothesis_not_a_precondition` — a bare inequality. This is `assumed_postcondition`'s "bound on an
intermediate" false positive, reclassified: not a defect to adjudicate, a conformance failure with a
prescribed fix. -/
@[lusterna_hoare]
theorem s8_bare_hypothesis (amt : U64) (s s' : St) (y : Unit)
    (hb    : amt.val < 100)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- `conclusion_not_a_postcondition` — a raw equation rather than a `Post … = ok true`. -/
@[lusterna_hoare]
theorem s9_conclusion_not_post (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    s'.total = amt := by sorry

/-- `predicate_not_failure_strict` — the postcondition can be satisfied by a failing measurement. -/
@[lusterna_hoare]
theorem s10_post_not_strict (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPostOpen amt s s' = ok true := by sorry

/-- `postcondition_ignores_output` — conforms to every other rule while saying nothing about what
`transfer` did. Note `y : Unit` is NOT demanded: a single-constructor-no-field type carries no
claim, so only `s'` counts. -/
@[lusterna_hoare]
theorem s11_post_ignores_output (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    PostIgnoresOutput amt s = ok true := by sorry

/-- `invariant_shape` — declared an invariant, but no hypothesis carries it on a pre-state. -/
@[lusterna_invariant]
theorem s12_invariant_shape (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    InvFlat s' = ok true := by sorry

/-- `predicate_not_failure_strict` via the invariant schema. -/
@[lusterna_invariant]
theorem s13_invariant_not_strict (amt : U64) (s s' : St) (y : Unit)
    (hinv  : InvSumOpen s = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    InvSumOpen s' = ok true := by sorry

/-- `no_schema_declared` — the ratchet. Unannotated must not be the cheap way past the check. -/
theorem s14_unannotated (amt : U64) (s s' : St) (y : Unit)
    (hexec : transfer amt s = ok (y, s')) :
    TransferPost amt s s' = ok true := by sorry

/-- `multiple_schemas_declared` — being checked against the laxer of two is not a result. -/
@[lusterna_invariant, lusterna_hoare]
theorem s15_double_annotated (amt : U64) (s s' : St) (y : Unit)
    (hinv  : InvFlat s = ok true)
    (hexec : transfer amt s = ok (y, s')) :
    InvFlat s' = ok true := by sorry

/-- CONFORMS with the real Aeneas shape: `outputLeaves` must descend through the neutral
`core.result.Result.Ok` wrapper to find `eff`, and `Post` mentions both outputs. -/
@[lusterna_hoare]
theorem s16_wrapped_outputs_conform (amt : U64) (s s' : St) (eff : Eff)
    (hexec : transferEff amt s = ok (.Ok eff, s')) :
    EffPost amt s eff s' = ok true := by sorry

/-- `postcondition_ignores_output` on a SUBSET: `vfinal` is used, `eff` is not. -/
@[lusterna_hoare]
theorem s17_ignores_one_of_two_outputs (amt : U64) (s s' : St) (eff : Eff)
    (hexec : transferEff amt s = ok (.Ok eff, s')) :
    EffPostIgnoresEff amt s s' = ok true := by sorry

end Probe.Schemas
