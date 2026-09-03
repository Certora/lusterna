import Aeneas
import Probe
open Aeneas Aeneas.Std Aeneas.Std.Result

-- Fixture for `assumed_postcondition` (`checkAssumedPostcondition`). A separate module because the check is about
-- a stateful subject `f : Input → State → Result (Output × State)`, which Probe.lean's
-- `Nat → Result Nat` helpers cannot express. Every theorem here is checked with
-- `#[`Probe.Taint.transfer]` as the target; `total`/`fee` stand in for MEASUREMENTS, which must
-- never seed taint no matter how their outputs are used.

namespace Probe.Taint

structure State where
  var   : Nat
  n     : Nat
  total : Nat

structure Out where
  ok : Bool

/-- THE SUBJECT. -/
def transfer (x : Nat) (s : State) : Result (Out × State) :=
  if x = 0 then fail .panic else ok (⟨true⟩, { s with n := s.n + x })

/-- A second subject-shaped step, for the chaining case. -/
def settle (o : Out) (s : State) : Result (Out × State) :=
  ok (o, { s with total := s.total + 1 })

/-- A MEASUREMENT: used to express properties, never the behaviour under test. -/
def total (s : State) : Result Nat := if s.total = 0 then fail .panic else ok s.total

/-- An INPUT-side predicate. Mentions only the pre-state, so it is always clean. -/
def Pre (x : Nat) (s : State) : Prop := 0 < x ∧ s.n ≤ 100

/-- A predicate OVER THE POST-STATE. Legitimate in a conclusion, a cheat in a hypothesis. -/
def Inv (s : State) : Prop := s.var ≤ s.total

-- ══ clean ════════════════════════════════════════════════════════════════════════

-- T1: the canonical accepted shape. Every hypothesis is about inputs and the pre-state; the only
-- thing said about s' is the conclusion.
theorem t1_canonical_good (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    s'.var = s.var := by sorry

-- T4: a precondition stated through a MEASUREMENT of the PRE-state. `total` is not a target, so
-- `t` is not tainted and `hk` is an ordinary input-side hypothesis. This is the shape that forced
-- targets to be explicit rather than inferred: with every `= ok` call seeding taint, `hk` would
-- be a violation, and it is one of the most common preconditions there is.
theorem t4_measurement_precondition (x k t : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hm : total s = ok t) (hk : k ≤ t) (hexec : transfer x s = ok (y, s')) :
    s'.var = s.var := by sorry

-- T5: CHAINED subject calls, with `settle` DECLARED as a continuation. That declaration is the
-- whole exemption -- see t5b, the same theorem without it.
theorem t5_chained_subject (x : Nat) (s s1 s2 : State) (y z : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s1)) (hstep : settle y s1 = ok (z, s2)) :
    s2.var = s.var := by sorry

-- T6a / T6b: the two TOTAL forms of a post-state measurement in the conclusion. Both are clean,
-- and both are the prescribed fix for t6's finding below -- a triple and its hand-written
-- equivalent. `forallTelescope` does not peel either, so each stays the conclusion.
theorem t6a_conclusion_triple (x k : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    total s' ⦃ m => k ≤ m ⦄ := by sorry

theorem t6b_conclusion_exists (x k : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    ∃ m, total s' = ok m ∧ k ≤ m := by sorry

-- T9: the TRIPLE form. The post-state is lambda-bound, never a telescope variable, so there is
-- nothing to taint -- and, crucially, this must return CLEAN rather than "no target execution
-- hypothesis found", which is what a naive `definingEq?`-only subject search would report.
theorem t9_triple_form (x : Nat) (s : State) (hpre : Pre x s) :
    transfer x s ⦃ r => r.2.var = s.var ⦄ := by sorry

-- ══ flagged ══════════════════════════════════════════════════════════════════════

-- T5b: t5 verbatim, but `settle` is NOT declared a continuation. `hstep` asserts that `settle`
-- SUCCEEDS on the post-state, which restricts the theorem -- so the chaining exemption is opt-in,
-- not a property of the shape.
theorem t5b_undeclared_continuation (x : Nat) (s s1 s2 : State) (y z : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s1)) (hstep : settle y s1 = ok (z, s2)) :
    s2.var = s.var := by sorry

-- T6: a measurement guard over the post-state, written as an implication in the CONCLUSION.
-- Vacuously true whenever `total s'` fails. No shape rule catches a guard sitting in a theorem's
-- own conclusion; the taint side reaches it instead. The fix is t6a/t6b above.
theorem t6_conclusion_side_measurement (x k : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    ∀ m, total s' = ok m → k ≤ m := by sorry

-- T13: A PREDICATE WEARING AN EXECUTION'S SHAPE. `hcheat` IS the postcondition:
-- `simp [checkVarPreserved] at hcheat; exact hcheat`. Nothing under verification runs here, yet
-- the binder matches `g args = ok out` exactly -- the hole that made "execution-shaped is
-- trusted" untenable.
def checkVarPreserved (before after : State) : Result Unit :=
  if after.var = before.var then ok () else fail .panic
theorem t13_predicate_as_call (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s'))
    (hcheat : checkVarPreserved s s' = ok ()) :
    s'.var = s.var := by sorry

-- T14: the Bool-returning form of the same trick.
def checkInvariant (s : State) : Result Bool := ok (s.var == 0)
theorem t14_bool_predicate_as_call (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s'))
    (hcheat : checkInvariant s' = ok true) :
    s'.var = s.var := by sorry

-- T15: no smuggled content at all -- just the SUCCESS assertion. Restricts the theorem to
-- post-states on which `total` happens to succeed, which is a weakening even though the
-- hypothesis says nothing else.
theorem t15_success_on_post_state (x t : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) (hok : total s' = ok t) :
    s'.var = s.var := by sorry

-- ── subject outputs must be FRESH: the execution binder must not pin its own result ──────────

-- T11: IN-PLACE state. `touch x s = ok (y, s)` asserts the post-state IS the pre-state -- stronger
-- than the preservation property it would be used to prove, and asserted rather than proved. `s`
-- stays untainted deliberately, so the finding lands here and not on `hpre`, which is innocent.
def touch (x : Nat) (s : State) : Result (Out × State) := ok (⟨true⟩, s)
theorem t11_in_place_state (x : Nat) (s : State) (y : Out)
    (hpre : Pre x s) (hexec : touch x s = ok (y, s)) :
    s.var = s.var := by sorry

-- T16: the OUTPUT VALUE is pinned to a constant. `⟨true⟩` is `Out.mk true`, and `Bool.true` is a
-- nullary constructor of a two-constructor type -- information, unlike `()`.
theorem t16_constant_output (x : Nat) (s s' : State)
    (hpre : Pre x s) (hexec : transfer x s = ok (⟨true⟩, s')) :
    s'.var = s.var := by sorry

-- T17: the POST-STATE is pinned structurally. `{ s with var := 10 }` is `State.mk 10 s.n s.total`,
-- so the execution binder decides the answer.
theorem t17_structured_output (x : Nat) (s : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, { s with var := 10 })) :
    (10 : Nat) = 10 := by sorry

-- ── an equality is an ASSUMPTION, never a definition ─────────────────────────────────────────

-- T18: the alias IS the cheat, with nothing downstream to catch. `exact h` closes it, and the
-- pre-fix exemption returned clean. Note `s` must stay untainted, or the finding would move onto
-- `hpre` and point at the wrong hypothesis.
theorem t18_alias_is_the_cheat (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) (h : s' = s) :
    s' = s := by sorry

-- T19: the goal reconstructed across TWO aliases, neither of which constrains anything on its own.
theorem t19_two_alias_reconstruction (x z : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s'))
    (h1 : z = s'.var) (h2 : z = s.var) :
    s'.var = s.var := by sorry

-- ── an assumption need not be `Prop`-typed ───────────────────────────────────────────────────

-- T20: the postcondition carried by a DATA binder. `h`'s type is a `Subtype`, not a proposition,
-- so an `isProp` filter never looks at it -- but `exact h.property` closes the goal.
theorem t20_subtype_carries_postcondition (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s'))
    (h : { _u : Unit // s'.var = s.var }) :
    s'.var = s.var := by sorry

-- T2: THE motivating case. Logically valid, provable by `exact hvar`, and it verifies nothing
-- about `transfer`. Nothing local separates it from the honest version. `is_conclusion`
-- must be true -- the hypothesis IS the goal.
theorem t2_assumes_its_own_conclusion (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) (hvar : s'.var = s.var) :
    s'.var = s.var := by sorry

-- T3: CLOSED TAINT. `hz` only NAMES the post-state's field -- taint flows through it but it is not
-- itself the cheat, so exactly one finding is expected here, on `hz2`.
theorem t3_closed_taint (x z : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) (hz : z = s'.var) (hz2 : 0 < z) :
    s'.var = s.var := by sorry

-- T7: INJECTIVITY -- the accepted false positive. `hab` is a hypothesis about two runs' outputs by
-- construction; there is no shape that separates it from a cheat. Declare such a theorem
-- `@[lusterna_lemma "why"]`, out of scope; flagged here, pinned so the trade-off stays deliberate.
theorem t7_injectivity_known_fp (x x' : Nat) (s sa sb : State) (a b : Out)
    (h1 : transfer x s = ok (a, sa)) (h2 : transfer x' s = ok (b, sb)) (hab : sa = sb) :
    x = x' := by sorry

-- T8: a BOUND ON AN INTERMEDIATE -- the other accepted false positive. A real overflow guard for a
-- downstream call, and simultaneously a domain restriction on the subject's own output.
theorem t8_intermediate_bound_known_fp (x : Nat) (s s1 s2 : State) (y z : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s1)) (hb : s1.n < 100)
    (hstep : settle y s1 = ok (z, s2)) :
    s2.var = s.var := by sorry

-- T10: taint hidden behind a NAMED PREDICATE over the post-state. No unfolding needed -- `s'`
-- occurs in the hypothesis either way.
theorem t10_tainted_named_predicate (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) (hinv : Inv s') :
    s'.var = s.var := by sorry

-- ── a second call may pin the subject's output without ever touching tainted ARGUMENTS ──────

-- T21: `expected` runs on clean inputs only, so an arguments-only test says it is a harmless
-- pre-state measurement -- but its OUTPUT is `y`, the subject's own result. If `expected x` is
-- easy to characterise, this determines `y` without reasoning about `transfer` at all.
def expected (x : Nat) : Result Out := ok ⟨x = 0⟩
theorem t21_output_pinned_by_clean_call (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) (hcheat : expected x = ok y) :
    y.ok = true := by sorry

-- ── an authorized continuation must bind fresh results too ──────────────────────────────────

-- T22: `settle` IS declared a continuation here, and still pins the post-state: `ok (z, s1)` says
-- the continuation left the state exactly as the subject produced it. Authorization permits the
-- chaining edge, not the constraint.
theorem t22_continuation_output_not_fresh (x : Nat) (s s1 : State) (y z : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s1)) (hstep : settle y s1 = ok (z, s1)) :
    s1.var = s.var := by sorry

-- ── a target is a provenance source, not an automatic continuation ───────────────────────────

-- T23: the subject run a SECOND time, on its own post-state. Being the target authorizes `transfer`
-- to introduce values, not to consume them: `hagain` assumes a further execution succeeds on s1,
-- which is the same assumption `rebalance s1 = ok ...` would be. Declaring `transfer` as its own
-- continuation is how an iterative theorem opts in -- see t24.
theorem t23_target_rerun_undeclared (x x2 : Nat) (s s1 s2 : State) (y z : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s1)) (hagain : transfer x2 s1 = ok (z, s2)) :
    s2.n = s.n + x + x2 := by sorry

-- T24: t23 with `transfer` declared as its own continuation -- clean. Also the regression for the
-- root-input computation: a global "anything a subject was handed" set would call s1 an input and
-- report BOTH executions as producing non-fresh output.
theorem t24_target_rerun_declared (x x2 : Nat) (s s1 s2 : State) (y z : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s1)) (hagain : transfer x2 s1 = ok (z, s2)) :
    s2.n = s.n + x + x2 := by sorry

-- ── choosing a constructor is information, fields or not ─────────────────────────────────────

-- T25: `some y` has already settled that the call returns `some` rather than `none`. Multi-
-- constructor wrappers pin a branch even when they carry a payload.
def maybe (x : Nat) (s : State) : Result (Option Out × State) := ok (some ⟨true⟩, s)
theorem t25_option_wrapper_pins_branch (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : maybe x s = ok (some y, s')) :
    s'.var = s.var := by sorry

-- ── two executions may not PRODUCE the same variable ────────────────────────────────────────

-- T26: injectivity's `hab : sa = sb` with the equality encoded in the binder names instead. Every
-- local test passes -- each output is canonical, neither reuses its own call's arguments -- so only
-- output ownership sees it. Sharing an output variable must not be a cheaper way to write the
-- premise than stating it.
theorem t26_shared_output_between_subjects (x x2 : Nat) (s sa : State) (a : Out)
    (h1 : transfer x s = ok (a, sa)) (h2 : transfer x2 s = ok (a, sa)) :
    x = x2 := by sorry

-- T27: a declared continuation landing its result back on a ROOT INPUT. `s` existed before any
-- target ran, so this constrains the continuation rather than naming its result.
def reset (o : Out) (s : State) : Result State := ok s
theorem t27_continuation_output_aliases_root_input (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) (hg : reset y s' = ok s) :
    s'.var = s.var := by sorry

-- ── the conclusion is exempt, so a named predicate is where the cheat moves ──────────────────

-- T28: nothing in the telescope touches s'; the antecedent lives inside the postcondition, and
-- `intro h; exact h` closes it.
def WeakPreservation (s s' : State) : Prop := s'.var = s.var → s'.var = s.var
theorem t28_predicate_hides_antecedent (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    WeakPreservation s s' := by sorry

-- T29: control -- a bounded quantification over the POST-state. `i < s'.n` guards `i`, a variable
-- the predicate itself introduced; it assumes nothing about s'. Must stay clean, or every
-- quantified postcondition becomes a finding.
def BoundedClaim (s' : State) : Prop := ∀ i, i < s'.n → i ≤ s'.n
theorem t29_quantifier_guard_is_not_an_antecedent (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    BoundedClaim s' := by sorry

-- ── a declared continuation on CLEAN arguments is an ordinary precondition ───────────────────

-- T30: `settle` is declared a continuation for this theorem, and is also called on the PRE-state
-- with a pinned output. Being on the continuation list must not impose freshness on a call that
-- never touches target-derived data -- that call is just a precondition.
theorem t30_declared_continuation_on_clean_args (x : Nat) (s s0 s' : State) (y w : Out)
    (hpre : Pre x s) (hprecheck : settle w s = ok (⟨true⟩, s0)) (hw : w.ok = true)
    (hexec : transfer x s = ok (y, s')) :
    s'.var = s.var := by sorry

-- ── a triple's postcondition can assume what it claims ──────────────────────────────────────

-- T31: the post-state is lambda-bound, so no telescope binder constrains it -- and the earlier
-- "structurally impossible" claim was simply wrong: the postcondition assumes its own conclusion,
-- closed by `intro h; exact h`. This returned CLEAN.
theorem t31_triple_postcondition_assumes_itself (x : Nat) (s : State) (hpre : Pre x s) :
    transfer x s ⦃ r => r.2.var = s.var → r.2.var = s.var ⦄ := by sorry

-- T32: the same, moved behind a named predicate reached from inside the postcondition lambda.
theorem t32_triple_postcondition_hides_antecedent (x : Nat) (s : State) (hpre : Pre x s) :
    transfer x s ⦃ r => WeakPreservation s r.2 ⦄ := by sorry

-- ── the conclusion is compositional, not just a root definition ──────────────────────────────

-- T33: `WeakPreservation s s' ∧ True`. The ROOT is `And`, a trusted inductive, so a root-keyed
-- unfold walked away without ever looking at the predicate.
theorem t33_conclusion_behind_a_connective (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    WeakPreservation s s' ∧ True := by sorry

-- T34: three wrapper definitions deep -- past the old depth-two cutoff, which returned clean.
def P3 (s s' : State) : Prop := WeakPreservation s s'
def P2 (s s' : State) : Prop := P3 s s'
def P1 (s s' : State) : Prop := P2 s s'
theorem t34_conclusion_beyond_old_depth_cap (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    P1 s s' := by sorry

-- T35: the antecedent carried by a Prop-valued STRUCTURE field in the conclusion -- the same
-- constructor-field case the predicate walk handles, now shared.
structure WeakStruct (s s' : State) : Prop where
  h : s'.var = s.var → s'.var = s.var
theorem t35_conclusion_structure_field (x : Nat) (s s' : State) (y : Out)
    (hpre : Pre x s) (hexec : transfer x s = ok (y, s')) :
    WeakStruct s s' := by sorry

-- ── Aeneas's own Rust-result wrapper is neutral; a crate's own branch choice is not ──────────

-- T36: the shape EVERY theorem about a fallible Rust function has. Aeneas translates
-- `fn f() -> Result<T, E>` to a nested `Result (core.result.Result T E × State)`, so `.Ok` here is
-- the translation's own structure rather than a claim the author made. Matching the allowlist by
-- exact name missed it -- the emitted constant is `Aeneas.Std.core.result.Result.Ok`, not the bare
-- `core.result.Result.Ok` -- and every such theorem drew a spurious "output pins a value".
def rustCall (x : Nat) (s : State) : Result (core.result.Result Unit Unit × State) :=
  ok (.Ok (), { s with n := s.n + x })
theorem t36_aeneas_result_wrapper_is_neutral (x : Nat) (s s' : State)
    (hpre : Pre x s) (hexec : rustCall x s = ok (.Ok (), s')) :
    s'.var = s.var := by sorry

-- ══ skipped ══════════════════════════════════════════════════════════════════════

-- T12: targets given, but this theorem never runs one. Reporting SKIPPED rather than returning 0
-- is what keeps "clean" from meaning "never ran" -- the failure mode of requiring explicit
-- targets instead of inferring them.
theorem t12_no_subject_execution (s : State) (t : Nat) (hm : total s = ok t) (hk : 0 < t) :
    0 < t := by sorry

end Probe.Taint
