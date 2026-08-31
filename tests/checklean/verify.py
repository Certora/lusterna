#!/usr/bin/env python3
"""Verify arbitrary theorems against the mechanical checks in `LusternaChecks.lean`.

Two modes:

  python tests/checklean/verify.py
      Runs the regression fixture (tests/checklean/*.lean) and asserts the exact expected finding
      set, including each schema_conformance finding's `rule`. This is what CI should run.

  python tests/checklean/verify.py --file mytheorem.lean --targets Foo.bar,Foo.baz
                                   [--subjects Foo.transfer] [--deps a.lean b.lean]
      Builds an arbitrary .lean file (plus optional dependency files) as a throwaway crate and
      prints whatever the checks find on the named theorems (`--subjects` adds the target-aware checks, which must
      be told which functions are under test) — raw output, no pass/fail
      judgment, since correctness here is exactly what a human or agent is meant to decide. This
      is the literal "verify an arbitrary theorem" tool: point it at any snippet and any theorem
      name and see what fires.

Both modes emit the EXACT driver documented for agents in docs/skills/mechanical-checks.md — same
imports, same `maxRecDepth`, same three `check*` calls per target, same bare `LUSTERNA_CHECK_DONE`
sentinel, read the same way ("the driver finished, so silence means clean"). Nothing here bypasses
that path for a shortcut answer, so a documented invocation that does not work fails here first.

Requires Docker and the `lusterna-toolchain:latest` image.
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lusterna import container, lean, tools                      # noqa: E402
from lusterna.schemas import AgentDeps                            # noqa: E402

HERE = Path(__file__).parent
CONTAINER_NAME = "lusterna-checklean-verify"

_CHECK_RE = re.compile(r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK\s+(\{.*\})\s*$")
# The JSON payload is OPTIONAL: the invocation documented for agents prints a bare
# `LUSTERNA_CHECK_DONE` sentinel, and this harness must accept exactly what that produces.
_DONE_RE = re.compile(r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK_DONE(?:\s+(\{.*\}))?\s*$")
_SKIP_RE = re.compile(r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK_SKIPPED\s+(\{.*\})\s*$")

# Every finding the fixture must produce, keyed by (check, theorem-or-predicate). This is the
# regression suite for the checker itself — see Fixture.lean for why each row is (not) expected.
EXPECTED = {
    # a predicate named under Aeneas's `<fn>.spec` convention -- the triple exclusion must not
    # swallow it (it matches the real `Aeneas.Std.WP.spec` constant, not the name suffix).
    # THE GUARD IS THE ANTECEDENT, not a shape two binders wide. An extra binder between the output
    # and its equation (V12), no bound output at all (V13), or the success equality tucked into a
    # conjunction (V17) are the same fail-open property and all used to walk past the matcher.
    # A TRIPLE'S POSTCONDITION is not covered by the triple's own success requirement (V14).
    # FOUR WRAPPERS DEEP (V15) — past the old three-round breadth-first cap, which returned its
    # partial result as though it were a complete clean one.
    # A DECLARATION NAMED UNDER A TRUSTED NAMESPACE (V16) is still project code: trust follows the
    # module a declaration was compiled into, which generated code cannot pick.
    # IN THE CONCLUSION, surviving the peel inside a conjunction (V18).
    # THE DEMAND, not the syntax. V19 hides the equation behind a predicate used as the antecedent —
    # invisible from either side alone, since at the use site it is not an equation and inside the
    # predicate it is a body. V20 hides it behind an existential. Both are unfolded/traversed at the
    # USE SITE now. (V22 is the control: a DATA-valued function type whose domain is an equation is
    # not an implication and must stay clean.)
    # A PROP-VALUED STRUCTURE keeps its propositions in constructor fields, not in a body (V21).

    # ── Taint.lean: `assumed_postcondition`, the information-flow rule ───────────────────────
    # t2 is the motivating case: nothing local separates it from the honest version, since the
    # execution is real and `Pre x s` genuine. The finding must carry "is_conclusion": true.
    ("assumed_postcondition", "Probe.Taint.t2_assumes_its_own_conclusion"),
    # taint closes through the alias `hz : z = s'.var`, and it fires on BOTH the alias (which carries
    # the taint) and `hz2 : 0 < z` (what it enables) — TWO findings, pinned in EXPECTED_COUNTS.
    ("assumed_postcondition", "Probe.Taint.t3_closed_taint"),
    # KNOWN FALSE POSITIVES, both accepted and pinned. Injectivity's `hab : sa = sb` is a
    # hypothesis about two runs' outputs by construction; a bound on
    # an intermediate is a real overflow guard AND a domain restriction on the subject's output.
    ("assumed_postcondition", "Probe.Taint.t7_injectivity_known_fp"),
    ("assumed_postcondition", "Probe.Taint.t8_intermediate_bound_known_fp"),
    ("assumed_postcondition", "Probe.Taint.t10_tainted_named_predicate"),
    # A FALLIBLE CALL ON THE POST-STATE is not a neutral observation, so being execution-shaped is
    # not enough to be trusted. t13/t14 smuggle the whole postcondition inside one
    # (`simp [checkVarPreserved] at hcheat; exact hcheat`); t15 smuggles nothing and is still a
    # weakening, because it assumes `total` succeeds on s'; t5b is a genuine next step that simply
    # was not declared as a continuation; t6 is the same shape in the conclusion, reached from the
    # taint side (fix: t6a/t6b).
    ("assumed_postcondition", "Probe.Taint.t5b_undeclared_continuation"),
    ("assumed_postcondition", "Probe.Taint.t6_conclusion_side_measurement"),
    ("assumed_postcondition", "Probe.Taint.t13_predicate_as_call"),
    ("assumed_postcondition", "Probe.Taint.t14_bool_predicate_as_call"),
    ("assumed_postcondition", "Probe.Taint.t15_success_on_post_state"),
    # A SUBJECT EXECUTION MUST PRODUCE FRESH, UNPINNED OUTPUTS. t11's `ok (y, s)` asserts the
    # post-state IS the pre-state; t16 pins the output value to a constant; t17 pins the post-state
    # structurally. Each decides in the execution binder what the conclusion should have claimed.
    ("assumed_postcondition", "Probe.Taint.t11_in_place_state"),
    ("assumed_postcondition", "Probe.Taint.t16_constant_output"),
    ("assumed_postcondition", "Probe.Taint.t17_structured_output"),
    # AN EQUALITY IS AN ASSUMPTION, not a definition. t18's alias is the whole cheat with nothing
    # downstream to catch it; t19 rebuilds the goal across two of them. Both propagate taint AND
    # are reported -- t3 accordingly fires twice, on the alias and on what it enables.
    ("assumed_postcondition", "Probe.Taint.t18_alias_is_the_cheat"),
    ("assumed_postcondition", "Probe.Taint.t19_two_alias_reconstruction"),
    # A DATA BINDER can carry a proposition. `Meta.isProp` is not a security boundary.
    ("assumed_postcondition", "Probe.Taint.t20_subtype_carries_postcondition"),
    # A SECOND CALL CAN PIN THE SUBJECT'S OUTPUT WITHOUT TOUCHING A TAINTED ARGUMENT — t21's
    # `expected x = ok y` runs on clean inputs and determines `y` anyway, so exemption tests the
    # whole binder type rather than the argument list.
    ("assumed_postcondition", "Probe.Taint.t21_output_pinned_by_clean_call"),
    # An AUTHORIZED CONTINUATION gets the chaining edge, not a licence to constrain: t22 declares
    # `settle` and still pins the post-state in its own execution binder.
    ("assumed_postcondition", "Probe.Taint.t22_continuation_output_not_fresh"),
    # A TARGET IS A PROVENANCE SOURCE, NOT AN AUTOMATIC CONTINUATION. t23 re-runs the subject on its
    # own post-state without declaring it; t24 is the same theorem with the declaration, and clean.
    ("assumed_postcondition", "Probe.Taint.t23_target_rerun_undeclared"),
    # CHOOSING A CONSTRUCTOR is information whether or not it carries fields.
    ("assumed_postcondition", "Probe.Taint.t25_option_wrapper_pins_branch"),
    # OUTPUT OWNERSHIP. Freshness is local to one call, so two executions PRODUCING the same
    # variable is a way to write injectivity's relational premise without writing one (t26); and a
    # continuation may not land its result on a value that predates every target (t27).
    ("assumed_postcondition", "Probe.Taint.t26_shared_output_between_subjects"),
    ("assumed_postcondition", "Probe.Taint.t27_continuation_output_aliases_root_input"),
    # THE CONCLUSION IS EXEMPT, so a named predicate is where the antecedent moves (t28). t29 is the
    # carve-out that keeps that from flagging every bounded quantification over the post-state.
    ("assumed_postcondition", "Probe.Taint.t28_predicate_hides_antecedent"),
    # A TRIPLE'S POSTCONDITION can assume what it claims. The post-state is lambda-bound, which was
    # wrongly documented as making the defect structurally impossible; both of these were clean.
    ("assumed_postcondition", "Probe.Taint.t31_triple_postcondition_assumes_itself"),
    ("assumed_postcondition", "Probe.Taint.t32_triple_postcondition_hides_antecedent"),
    # THE CONCLUSION IS COMPOSITIONAL. t33 hides the predicate behind `And` (a trusted inductive, so
    # a root-keyed unfold never looked); t34 sits three wrappers past the old depth-two cutoff;
    # t35 carries the antecedent in a Prop-valued structure field.
    ("assumed_postcondition", "Probe.Taint.t33_conclusion_behind_a_connective"),
    ("assumed_postcondition", "Probe.Taint.t34_conclusion_beyond_old_depth_cap"),
    ("assumed_postcondition", "Probe.Taint.t35_conclusion_structure_field"),
    # The quantifier carve-out is no longer SILENT: a bounded quantification over the post-state is
    # reported at low signal ("quantifier over the post-state"), because "mentions a local binder"
    # is bypassable — `∀ i, (i = i → P s') → P s'` satisfies it and is still a free assumption.
    ("assumed_postcondition", "Probe.Taint.t29_quantifier_guard_is_not_an_antecedent"),

    # ── Invariant.lean: `invariant_not_strict` ─────────────────────────────────────────────────
    # fail_open — a Result value is handed to something that can discard its failure.
    ("invariant_not_strict", "Probe.Inv.Spec.i11_sum_open"),    # `ok (! ok? (sum_bals …))`
    ("invariant_not_strict", "Probe.Inv.Spec.i12_match_res"),   # match ON a Result
    ("invariant_not_strict", "Probe.Inv.Spec.i13_ite_okq"),     # Result in an `ite` condition
    ("invariant_not_strict", "Probe.Inv.Spec.i14_let_res"),     # non-monadic `let` of a Result
    ("invariant_not_strict", "Probe.Inv.Spec.i15_deep"),        # 4 wrappers above a fail-open leaf
    ("invariant_not_strict", "Probe.Inv.Spec.i16_cap_bad"),     # same, with an extra argument
    # not_certified — no evidence of a defect, only that the walk cannot certify.
    ("invariant_not_strict", "Probe.Inv.Spec.i17_opaque"),
    ("invariant_not_strict", "Probe.Inv.Spec.i18_partial"),
    ("invariant_not_strict", "Probe.Inv.Spec.i19_foldm"),       # `List.foldlM`, see the fixture

    # ── Schemas.lean: `schema_conformance` — one broken rule per theorem ───────────────────────
    ("schema_conformance", "Probe.Schemas.s5_two_executions"),
    ("schema_conformance", "Probe.Schemas.s6_pinned_output"),
    ("schema_conformance", "Probe.Schemas.s7_pre_mentions_output"),
    ("schema_conformance", "Probe.Schemas.s8_bare_hypothesis"),
    ("schema_conformance", "Probe.Schemas.s9_conclusion_not_post"),
    ("schema_conformance", "Probe.Schemas.s10_post_not_strict"),
    ("schema_conformance", "Probe.Schemas.s11_post_ignores_output"),
    ("schema_conformance", "Probe.Schemas.s12_invariant_shape"),
    ("schema_conformance", "Probe.Schemas.s13_invariant_not_strict"),
    ("schema_conformance", "Probe.Schemas.s14_unannotated"),
    ("schema_conformance", "Probe.Schemas.s15_double_annotated"),
    ("schema_conformance", "Probe.Schemas.s17_ignores_one_of_two_outputs"),
    # precedence: the shape finding, and ONLY it, on a theorem that is also non-strict
    ("schema_conformance", "Probe.Schemas.g1_precedence_shape_first"),
    ("schema_conformance", "Probe.Schemas.g4_invariant_with_side_condition"),
    # g2/g3 are absent deliberately — `freeform` puts the two documented false positives of
    # `assumed_postcondition` out of scope, which is what lets the shape rules block at all.

    # ── Legit.lean: `assumption_legitimacy` ────────────────────────────────────────────────────
    # deposit_monotone's type references the target `deposit` → flagged; limbMul_spec (substrate) is
    # the clean control, absent here.
    ("assumption_legitimacy", "Probe.Legit.deposit_monotone"),
}

# `schema_conformance`'s `rule` field is the whole point of the check — a finding that fires for the wrong reason
# is not a pass. Pinned per theorem, so a rule that starts mis-attributing shows up here.
EXPECTED_SCHEMA_RULES = {
    "Probe.Schemas.s5_two_executions":      "execution_not_unique",
    "Probe.Schemas.s6_pinned_output":       "execution_output_not_fresh",
    "Probe.Schemas.s7_pre_mentions_output": "precondition_mentions_output",
    "Probe.Schemas.s8_bare_hypothesis":     "hypothesis_not_a_precondition",
    "Probe.Schemas.s9_conclusion_not_post": "conclusion_not_a_postcondition",
    "Probe.Schemas.s10_post_not_strict":    "predicate_not_failure_strict",
    "Probe.Schemas.s11_post_ignores_output": "postcondition_ignores_output",
    "Probe.Schemas.s12_invariant_shape":    "invariant_shape",
    "Probe.Schemas.s13_invariant_not_strict": "predicate_not_failure_strict",
    "Probe.Schemas.s14_unannotated":        "no_schema_declared",
    "Probe.Schemas.s15_double_annotated":   "multiple_schemas_declared",
    "Probe.Schemas.s17_ignores_one_of_two_outputs": "postcondition_ignores_output",
    "Probe.Schemas.g1_precedence_shape_first":       "execution_not_unique",
    "Probe.Schemas.g4_invariant_with_side_condition": "extraneous_hypothesis",
}
# Taint.lean's t1/t4/t5/t6a/t6b/t9/t24/t30/t36 are absent on purpose — they are
# `assumed_postcondition`'s clean controls: the canonical shape, a pre-state measurement
# precondition, a chain through a DECLARED continuation, the two total forms of a post-state
# measurement in the conclusion (the prescribed fix for t6), the triple, and a target re-run with
# the target DECLARED as its own continuation.
#
# Several Taint theorems fire more than once (t3's alias plus what it enables, t19's two aliases,
# t26's two subjects producing the same output), so the finding COUNT exceeds the number of EXPECTED
# rows. The exact multiplicities are pinned per theorem in EXPECTED_COUNTS.
#
# b16/b17/b18 (proof and implicit-type arguments) are absent on purpose — a proof term is not an
# input, and an implicit type is reached through the other binders' types; both must stay clean.
#
# `PureNat`/`GoodConj`/`GoodTriple`-style controls are likewise absent — clean is correct.
EXPECTED_FINDING_COUNT = 56   # assumed_postcondition's 32 + invariant_not_strict's 9
                              # + schema_conformance's 14 (12 + precedence + extraneous-hypothesis)
                              # + assumption_legitimacy's 1 (deposit_monotone)

# ── Attribute-level expectations (beyond membership) ─────────────────────────────────────────────
# Membership pins WHICH theorems fire; these pin the rest. A regression that keeps the same theorems
# firing but changes WHY/HOW — a real fail-open defect silently downgraded to "cannot certify", a
# finding migrated to the wrong hypothesis, or two findings collapsing into one — passes a
# membership-only check. These close that gap.

# `invariant_not_strict` carries its severity in the `reason` field: `fail_open` = a Result value is
# genuinely handed to something that can drop its failure (a real defect); `not_certified` = the walk
# could not certify (no evidence of a defect). Silently swapping one for the other is a verdict change.
EXPECTED_INVARIANT_SEVERITY = {
    "Probe.Inv.Spec.i11_sum_open":  "fail_open",
    "Probe.Inv.Spec.i12_match_res": "fail_open",
    "Probe.Inv.Spec.i13_ite_okq":   "fail_open",
    "Probe.Inv.Spec.i14_let_res":   "fail_open",
    "Probe.Inv.Spec.i15_deep":      "fail_open",
    "Probe.Inv.Spec.i16_cap_bad":   "fail_open",
    "Probe.Inv.Spec.i17_opaque":    "not_certified",
    "Probe.Inv.Spec.i18_partial":   "not_certified",
    "Probe.Inv.Spec.i19_foldm":     "not_certified",
}

# `assumed_postcondition`'s `is_conclusion: true` marks the strongest form — a hypothesis that
# restates the CONCLUSION itself (closed by `exact hvar`). Only t2 and t18 qualify; every other
# finding is hypothesis-side. Pinned both ways (a control `false`) so a mis-classification in either
# direction is caught.
EXPECTED_IS_CONCLUSION = {
    "Probe.Taint.t2_assumes_its_own_conclusion": True,
    "Probe.Taint.t18_alias_is_the_cheat":        True,
    "Probe.Taint.t15_success_on_post_state":     False,
    "Probe.Taint.t28_predicate_hides_antecedent": False,
}

# Per-(check, theorem) finding counts. Any EXPECTED key not listed here must fire EXACTLY once; the
# ones below fire more. This pins the COMPOSITION of the total, so a finding migrating between two
# EXPECTED theorems (which leaves the scalar total unchanged) is caught.
EXPECTED_COUNTS = {
    ("assumed_postcondition", "Probe.Taint.t3_closed_taint"):                2,
    ("assumed_postcondition", "Probe.Taint.t19_two_alias_reconstruction"):   2,
    ("assumed_postcondition", "Probe.Taint.t26_shared_output_between_subjects"): 2,
}

# For the twice-firing taint theorems, the DISTINCT hypotheses each fires on (substring match, robust
# to pretty-printer spacing). Pins that the two findings land on the two right binders — so a collapse
# to one, or a shift onto the wrong binder, is caught at the attribute level, not just by the count.
EXPECTED_HYPOTHESES = {
    ("assumed_postcondition", "Probe.Taint.t3_closed_taint"):              {"z = s'.var", "0 < z"},
    ("assumed_postcondition", "Probe.Taint.t19_two_alias_reconstruction"): {"z = s'.var", "z = s.var"},
}

# `assumed_postcondition` only — (theorem, subjects, continuations). It must be told which functions
# are under test, so it runs on the Taint module with an explicit subject.
# `t11` names `touch` because that is ITS subject; `t12` names a target it never calls, which is
# exactly the SKIPPED case pinned below.
TAINT_THEOREMS = [
    # THE RECONCILIATION, pinned rather than asserted. Three documents now claim checks 2 and 3
    # do not contradict each other on the `Inv … = ok true` shape: the PRE-state form is a clean
    # hypothesis (its output is the pinned literal `true`, so it taints nothing) and the POST-state
    # form is the conclusion, which `assumed_postcondition` exempts. If either of these fires, that claim is wrong
    # in mechanical-checks.md, aeneas-lean-core.md and README.md, and the text must change.
    ("Probe.Inv.Spec.i1_flat", ["Probe.Inv.transfer"], []),
    ("Probe.Inv.Spec.i10_sum", ["Probe.Inv.transfer"], []),
    ("Probe.Taint.t1_canonical_good", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t2_assumes_its_own_conclusion", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t3_closed_taint", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t4_measurement_precondition", ["Probe.Taint.transfer"], []),
    # the ONLY two rows that declare a continuation — t5b is t5 without one, and t8 declares it so
    # its finding stays isolated to the intermediate bound it is actually about.
    ("Probe.Taint.t5_chained_subject", ["Probe.Taint.transfer"], ["Probe.Taint.settle"]),
    ("Probe.Taint.t5b_undeclared_continuation", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t6_conclusion_side_measurement", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t6a_conclusion_triple", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t6b_conclusion_exists", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t7_injectivity_known_fp", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t8_intermediate_bound_known_fp", ["Probe.Taint.transfer"], ["Probe.Taint.settle"]),
    ("Probe.Taint.t9_triple_form", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t10_tainted_named_predicate", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t12_no_subject_execution", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t13_predicate_as_call", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t14_bool_predicate_as_call", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t15_success_on_post_state", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t20_subtype_carries_postcondition", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t11_in_place_state", ["Probe.Taint.touch"], []),
    ("Probe.Taint.t21_output_pinned_by_clean_call", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t22_continuation_output_not_fresh", ["Probe.Taint.transfer"], ["Probe.Taint.settle"]),
    ("Probe.Taint.t23_target_rerun_undeclared", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t24_target_rerun_declared", ["Probe.Taint.transfer"], ["Probe.Taint.transfer"]),
    ("Probe.Taint.t25_option_wrapper_pins_branch", ["Probe.Taint.maybe"], []),
    ("Probe.Taint.t26_shared_output_between_subjects", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t27_continuation_output_aliases_root_input", ["Probe.Taint.transfer"], ["Probe.Taint.reset"]),
    ("Probe.Taint.t28_predicate_hides_antecedent", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t29_quantifier_guard_is_not_an_antecedent", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t30_declared_continuation_on_clean_args", ["Probe.Taint.transfer"], ["Probe.Taint.settle"]),
    ("Probe.Taint.t31_triple_postcondition_assumes_itself", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t32_triple_postcondition_hides_antecedent", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t33_conclusion_behind_a_connective", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t34_conclusion_beyond_old_depth_cap", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t35_conclusion_structure_field", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t36_aeneas_result_wrapper_is_neutral", ["Probe.Taint.rustCall"], []),
    ("Probe.Taint.t19_two_alias_reconstruction", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t18_alias_is_the_cheat", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t17_structured_output", ["Probe.Taint.transfer"], []),
    ("Probe.Taint.t16_constant_output", ["Probe.Taint.transfer"], []),
]

# `LUSTERNA_CHECK_SKIPPED` records that MUST appear, keyed like EXPECTED. Any other one fails the
# run: "clean" has to mean "checked", and a skip means part of the run never happened.
EXPECTED_SKIPPED = {
    ("assumed_postcondition", "Probe.Taint.t12_no_subject_execution"),
    # `invariant_not_strict`'s three NEAR MISSES: the conclusion is `Inv args = ok true` but the rest of the
    # invariant-preservation shape is absent. Pinned as SKIPPED rather than silent because this is
    # exactly the case where a judge believes the check ran on their invariant and it did not.
    ("invariant_not_strict", "Probe.Inv.Spec.i23_no_pre_hypothesis"),
    ("invariant_not_strict", "Probe.Inv.Spec.i24_two_differences"),
    ("invariant_not_strict", "Probe.Inv.Spec.i25_not_a_target"),
    # An INLINE invariant lands here too, and usefully so: the conclusion `(do …) = ok true` does
    # match the claim shape, but its head is `Bind.bind` — library code, nothing project-local to
    # walk. So the judge is told "name your invariant as a `def`" instead of reading silence.
    ("invariant_not_strict", "Probe.Inv.Spec.i22_inline_invariant"),
    ("invariant_not_strict", "Probe.Inv.Spec.i26_compound_pre_state"),
}

# `invariant_not_strict` only — (theorem, targets). Every theorem in Invariant.lean, so the clean controls are
# exercised by the same run as the findings: a check that silently stopped firing would otherwise
# still "pass".
INVARIANT_THEOREMS = [(f"Probe.Inv.Spec.{n}", ["Probe.Inv.transfer", "Probe.Inv.transferArr"])
                      for n in [
    # certified strict — must draw nothing
    "i1_flat", "i2_assert", "i3_fold", "i4_wf", "i5_ite", "i6_dite", "i7_match_pure",
    "i8_of_option", "i9_cap_agrees", "i10_sum", "i27_array_deep_chain",
    "i28_partial_fixpoint_loop",
    # fail_open
    "i11_sum_open", "i12_match_res", "i13_ite_okq", "i14_let_res", "i15_deep", "i16_cap_bad",
    # not_certified
    "i17_opaque", "i18_partial", "i19_foldm",
    # shape gate — silent
    "i20_ordinary_theorem", "i21_prop_invariant",
    # shape gate — near miss, SKIPPED
    "i22_inline_invariant", "i23_no_pre_hypothesis", "i24_two_differences", "i25_not_a_target",
    "i26_compound_pre_state",
]]

# `schema_conformance` only — (theorem, targets). Every theorem in Schemas.lean, conforming ones included, so a
# check that silently stopped firing cannot still "pass".
SCHEMA_THEOREMS = [(f"Probe.Schemas.{n}", ["Probe.Inv.transfer", "Probe.Schemas.transferEff"])
                   for n in [
    # conforming — must draw nothing
    "s1_hoare_conforms", "s2_hoare_no_precondition", "s3_invariant_conforms", "s4_freeform",
    # one broken rule each
    "s5_two_executions", "s6_pinned_output", "s7_pre_mentions_output", "s8_bare_hypothesis",
    "s9_conclusion_not_post", "s10_post_not_strict", "s11_post_ignores_output",
    "s12_invariant_shape", "s13_invariant_not_strict", "s14_unannotated", "s15_double_annotated",
    # the real Aeneas output shape: two informative outputs, one behind a neutral wrapper
    "s16_wrapped_outputs_conform", "s17_ignores_one_of_two_outputs",
    # the gate's own composition: precedence, and freeform scoping off the shape rules
    "g1_precedence_shape_first", "g2_injectivity_is_freeform", "g3_intermediate_bound_is_freeform",
    "g4_invariant_with_side_condition", "g5_invariant_bare_is_clean",
]]

# `assumption_legitimacy` — (assumption-module axioms to check, target patterns). `limbMul_spec` is
# the substrate control (must stay clean); `deposit_monotone` references the target `deposit` and must
# be flagged. See Legit.lean.
LEGIT_CHECK = (["Probe.Legit.limbMul_spec", "Probe.Legit.deposit_monotone"], ["deposit"])

# `checkDefAxioms` — the TRANSLATE-time opaque-footprint disclosure. Per target def, the NON-standard
# axioms its body TRANSITIVELY depends on. `leakyIndirect` never names `Op` itself — it reaches it
# through `leakyDirect` — so its presence here is the transitive-closure assertion (the Fraction/
# Display leak in miniature). See DefAx.lean.
EXPECTED_DEF_AXIOMS = {
    "Probe.DefAx.cleanDef":      [],
    "Probe.DefAx.leakyDirect":   ["Probe.DefAx.Op"],
    "Probe.DefAx.leakyIndirect": ["Probe.DefAx.Op"],
}



def sh(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(args)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def parse_findings(out: str) -> tuple[list[dict], list[dict], dict | None]:
    """Parse `LUSTERNA_CHECK` / `LUSTERNA_CHECK_SKIPPED` / `LUSTERNA_CHECK_DONE` lines. Mirrors
    exactly what an agent (or SPEC-JUDGE) sees reading the same `lake env lean` output — no hidden
    extra parsing power."""
    findings = [json.loads(m.group(1)) for m in _CHECK_RE.finditer(out)]
    skipped = [json.loads(m.group(1)) for m in _SKIP_RE.finditer(out)]
    done = _DONE_RE.search(out)
    if not done:
        return findings, skipped, None
    return findings, skipped, (json.loads(done.group(1)) if done.group(1) else {})


def run_checks(cid: str, lean_dir: str, import_lines: list[str],
               taint: list[tuple[str, list[str], list[str]]] | None = None,
               invariant: list[tuple[str, list[str]]] | None = None,
               schema: list[tuple[str, list[str]]] | None = None,
               legitimacy: tuple[list[str], list[str]] | None = None,
               ) -> tuple[list[dict], list[dict], dict | None, str]:
    """The exact invocation documented in docs/skills/mechanical-checks.md: write a tiny driver
    that imports the checker plus the target module(s), call the `check*` functions per theorem,
    run it with `lake env lean`. *taint* pairs a theorem with the SUBJECT functions
    `assumed_postcondition` needs told, plus any CONTINUATION functions it may legitimately chain
    through; *invariant* pairs a theorem with the TARGET functions `invariant_not_strict` needs told;
    *schema* likewise for `schema_conformance`. Every check now takes its own explicit theorem list —
    there is no longer a check that runs on "every theorem" with no configuration. Returns
    (findings, skipped-records,
    done-record-or-None, raw combined output) — `done is None` means the driver never finished (a
    real compile error), which the caller must treat as "could not check", never as clean."""
    body_lines = import_lines + [
        "open Lusterna.Checks",
        "set_option maxRecDepth 4000 in",
        "#eval show Lean.Meta.MetaM Unit from do",
    ]
    for t, subjects, conts in (taint or []):
        arr = ", ".join("`" + g for g in subjects)
        carr = ", ".join("`" + g for g in conts)
        body_lines.append(f"  let _ ← checkAssumedPostcondition `{t} #[{arr}] #[{carr}]")
    for t, tgts in (invariant or []):
        arr = ", ".join("`" + g for g in tgts)
        body_lines.append(f"  let _ ← checkInvariantTotality `{t} #[{arr}]")
    for t, tgts in (schema or []):
        arr = ", ".join("`" + g for g in tgts)
        # THE GATE, not the bare conformance check: `checkSpecGate` is what the harness runs, and
        # its precedence (shape finding alone) and freeform scoping only exist in the composition.
        body_lines.append(f"  let _ ← checkSpecGate `{t} #[{arr}]")
    if legitimacy is not None:
        axioms, tgts = legitimacy
        aarr = ", ".join("`" + a for a in axioms)
        tarr = ", ".join("`" + g for g in tgts)
        # `assumption_legitimacy` takes (axioms, targets), not (theorem, targets) — it is consumed by
        # the harness's `_record_axioms`, not the FORMALISE gate, but shares the record protocol.
        body_lines.append(f"  checkAssumptionLegitimacy #[{aarr}] #[{tarr}]")
    body_lines.append('  IO.println "LUSTERNA_CHECK_DONE"')
    body = "\n".join(body_lines) + "\n"
    rel = "_verify_driver.lean"
    assert not tools.write_out(AgentDeps(container_id=cid, repo_path=".", session_id="v",
                                         design_doc=""), f"lean/{rel}", body).startswith("ERROR:")
    code, out, err = container.exec_in(cid, ["lake", "env", "lean", rel], workdir=lean_dir, timeout=300)
    combined = out + "\n" + err
    findings, skipped, done = parse_findings(combined)
    if code != 0 and done is None:
        return [], [], None, combined
    return findings, skipped, done, combined


def run_fixture() -> int:
    sh("docker", "rm", "-f", CONTAINER_NAME, check=False)
    cid = sh("docker", "run", "--rm", "--detach", "--name", CONTAINER_NAME,
             "lusterna-toolchain:latest", "sleep", "infinity").strip()
    print(f"container {cid[:12]}")
    try:
        deps = AgentDeps(container_id=cid, repo_path=HERE, session_id="fixture",
                         design_doc="", campaign="Fixture")
        lean_dir = f"{container.OUT_IN}/lean"
        sh("docker", "exec", cid, "mkdir", "-p", f"{lean_dir}/Probe/Spec")
        for src, dst in [(HERE / "Probe.lean", "lean/Probe.lean"),
                         (HERE / "Fixture.lean", "lean/Probe/Spec/Fixture.lean"),
                         (HERE / "Taint.lean", "lean/Probe/Taint.lean"),
                         (HERE / "Invariant.lean", "lean/Probe/Invariant.lean"),
                         (HERE / "Schemas.lean", "lean/Probe/Schemas.lean"),
                         (HERE / "DefAx.lean", "lean/Probe/DefAx.lean"),
                         (HERE / "Legit.lean", "lean/Probe/Legit.lean")]:
            assert not tools.write_out(deps, dst, src.read_text()).startswith("ERROR:")
        lean.setup_lake(deps)   # writes the lakefile AND LusternaChecks.lean, exactly as in prod

        print("lake build ...")
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            return sys.exit(f"fixture did not build:\n{out}\n{err}")

        findings, skipped, done, _ = run_checks(
            cid, lean_dir,
            ["import Probe.LusternaChecks", "import Probe.Spec.Fixture",
             "import Probe.Taint", "import Probe.Invariant", "import Probe.Schemas",
             "import Probe.Legit"],
            taint=TAINT_THEOREMS, invariant=INVARIANT_THEOREMS, schema=SCHEMA_THEOREMS,
            legitimacy=LEGIT_CHECK)
        if done is None:
            return sys.exit("the driver file never completed — see raw output above")

        # A SKIPPED record means part of the run never happened while the finding set still looks
        # complete — the ambiguity the token exists to expose. Only the ones pinned in
        # EXPECTED_SKIPPED may appear, and each of those must actually appear.
        got_skipped = {(k.get("check", "?"), k.get("theorem") or k.get("predicate") or k.get("axiom", "?"))
                       for k in skipped}
        got = {(f["check"], f.get("theorem") or f.get("predicate") or f.get("axiom", "?"))
               for f in findings}
        print(f"\n{len(findings)} finding(s) (expected {EXPECTED_FINDING_COUNT}):")
        for f in sorted(findings, key=lambda d: (d["check"],
                        str(d.get("theorem") or d.get("predicate") or d.get("axiom")))):
            who = f.get("theorem") or f.get("predicate") or f.get("axiom")
            what = f.get("hypothesis") or f.get("guard") or f.get("detail") or f.get("reason")
            print(f"  [{f['check']}] {who}: {what}")

        missing = EXPECTED - got
        spurious = got - EXPECTED
        for label, d in [("MISSED (false negative)", sorted(missing)),
                         ("SPURIOUS (false positive)", sorted(spurious)),
                         ("SKIPPED, unexpected", sorted(got_skipped - EXPECTED_SKIPPED)),
                         ("SKIPPED, expected but absent", sorted(EXPECTED_SKIPPED - got_skipped))]:
            if d:
                print(f"\n{label}: {d}")
        # `schema_conformance`'s `rule` is the assertion, not just that a finding appeared: a conformance check
        # that fires for the wrong reason gives FORMALISE an unactionable retry message.
        rules = {f["theorem"]: f.get("rule") for f in findings if f["check"] == "schema_conformance"}
        rule_bad = {k: (rules.get(k), v) for k, v in EXPECTED_SCHEMA_RULES.items() if rules.get(k) != v}
        if rule_bad:
            print("\nWRONG RULE (theorem: got, expected):")
            for k, (got, exp) in sorted(rule_bad.items()):
                print(f"  {k}: {got!r} != {exp!r}")

        # ── attribute-level assertions (severity / is_conclusion / per-theorem count / hypothesis) ──
        sev = {f["theorem"]: f.get("reason") for f in findings if f["check"] == "invariant_not_strict"}
        sev_bad = {k: (sev.get(k), v) for k, v in EXPECTED_INVARIANT_SEVERITY.items() if sev.get(k) != v}
        if sev_bad:
            print("\nWRONG SEVERITY (theorem: got, expected):")
            for k, (got, exp) in sorted(sev_bad.items()):
                print(f"  {k}: {got!r} != {exp!r}")

        isc_bad = {}
        for f in findings:
            if f["check"] == "assumed_postcondition" and f["theorem"] in EXPECTED_IS_CONCLUSION:
                exp = EXPECTED_IS_CONCLUSION[f["theorem"]]
                if f.get("is_conclusion") != exp:
                    isc_bad[f["theorem"]] = (f.get("is_conclusion"), exp)
        if isc_bad:
            print("\nWRONG is_conclusion (theorem: got, expected):")
            for k, (got, exp) in sorted(isc_bad.items()):
                print(f"  {k}: {got!r} != {exp!r}")

        counts = Counter((f["check"], f.get("theorem") or f.get("predicate") or f.get("axiom", "?"))
                         for f in findings)
        count_bad = {k: (counts.get(k, 0), EXPECTED_COUNTS.get(k, 1))
                     for k in EXPECTED if counts.get(k, 0) != EXPECTED_COUNTS.get(k, 1)}
        if count_bad:
            print("\nWRONG per-theorem count (key: got, expected):")
            for k, (got, exp) in sorted(count_bad.items()):
                print(f"  {k}: {got} != {exp}")

        hyp_bad = {}
        for k, subs in EXPECTED_HYPOTHESES.items():
            hyps = [f.get("hypothesis", "") for f in findings
                    if (f["check"], f.get("theorem")) == k]
            missing_subs = [s for s in subs if not any(s in h for h in hyps)]
            if missing_subs:
                hyp_bad[k] = (missing_subs, hyps)
        if hyp_bad:
            print("\nMISSING HYPOTHESIS (key: expected-substrings not found, got):")
            for k, (subs, hyps) in sorted(hyp_bad.items()):
                print(f"  {k}: {subs} not in {hyps}")

        # ── checkDefAxioms: the TRANSLATE-time opaque-footprint disclosure ──────────────────────────
        # Its own driver (run_checks is spec-gate-shaped); asserts the exact transitive footprint.
        # checkDefAxioms takes dotted-SUFFIX patterns and discovers the matching crate defs in the
        # environment (like the legitimacy/schema checks) — pass the bare method names, not the
        # reconstructed constant names, so this exercises the same resolution path production uses.
        da_pats = ", ".join("`" + d.rsplit(".", 1)[-1] for d in EXPECTED_DEF_AXIOMS)
        da_body = ("import Probe.DefAx\nimport Probe.LusternaChecks\nopen Lusterna.Checks\n"
                   "set_option maxRecDepth 8000 in\n"
                   "#eval show Lean.Meta.MetaM Unit from do\n"
                   f"  checkDefAxioms #[{da_pats}]\n"
                   '  IO.println "LUSTERNA_CHECK_DONE"\n')
        assert not tools.write_out(deps, "lean/_defax_driver.lean", da_body).startswith("ERROR:")
        _, dout, derr = container.exec_in(cid, ["lake", "env", "lean", "_defax_driver.lean"],
                                          workdir=lean_dir, timeout=300)
        da_find, _, da_done = parse_findings(dout + "\n" + derr)
        got_footprint = {f["def"]: sorted(f.get("opaque", []))
                         for f in da_find if f.get("check") == "def_axioms"}
        footprint_ok = da_done is not None and got_footprint == EXPECTED_DEF_AXIOMS
        if not footprint_ok:
            print(f"\ncheckDefAxioms MISMATCH: got {got_footprint}, expected {EXPECTED_DEF_AXIOMS}")
        else:
            print(f"\ncheckDefAxioms OK: {got_footprint}")

        skip_ok = got_skipped == EXPECTED_SKIPPED and len(skipped) == len(EXPECTED_SKIPPED)
        count_ok = len(findings) == EXPECTED_FINDING_COUNT
        if not count_ok:
            print(f"\nCOUNT MISMATCH: got {len(findings)}, expected {EXPECTED_FINDING_COUNT}")
        ok = (not (missing or spurious or rule_bad or sev_bad or isc_bad or count_bad or hyp_bad)
              and skip_ok and count_ok and footprint_ok)
        print("\nPASS" if ok else "\nFAIL")
        return 0 if ok else 1
    finally:
        sh("docker", "rm", "-f", CONTAINER_NAME, check=False)


def run_arbitrary(file: Path, targets: list[str], deps_files: list[Path],
                  subjects: list[str], conts: list[str]) -> int:
    sh("docker", "rm", "-f", CONTAINER_NAME, check=False)
    cid = sh("docker", "run", "--rm", "--detach", "--name", CONTAINER_NAME,
             "lusterna-toolchain:latest", "sleep", "infinity").strip()
    print(f"container {cid[:12]}")
    try:
        deps = AgentDeps(container_id=cid, repo_path=".", session_id="arbitrary", design_doc="")
        lean_dir = f"{container.OUT_IN}/lean"
        sh("docker", "exec", cid, "mkdir", "-p", lean_dir)
        # The root module (lib name) is whichever file's stem you pass as --file; dependency files
        # land alongside it. This mirrors the crate-root convention every other stage uses.
        lib_name = file.stem
        assert not tools.write_out(deps, f"lean/{file.name}", file.read_text()).startswith("ERROR:")
        for d in deps_files:
            assert not tools.write_out(deps, f"lean/{d.name}", d.read_text()).startswith("ERROR:")
        lean.setup_lake(deps)

        print(f"lake build (lib «{lib_name}») ...")
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            return sys.exit(f"file did not build:\n{out}\n{err}")

        imports = [f"import {lib_name}.LusternaChecks", f"import {file.stem}"]
        # Checks 2 and 3 run only when --subjects names the function(s) under test; without them
        # `assumed_postcondition` has no taint source and the others no target, and all would report SKIPPED on every
        # theorem rather than anything useful.
        taint = [(t, subjects, conts) for t in targets] if subjects else None
        # The invariant and schema checks need the same "functions under test" list, so --subjects
        # enables them too.
        inv = [(t, subjects) for t in targets] if subjects else None
        findings, skipped, done, raw = run_checks(cid, lean_dir, imports, taint=taint,
                                                  invariant=inv, schema=inv)
        if done is None:
            print("\nDRIVER DID NOT COMPLETE — could not check (not evidence either way):")
            print(raw[-2000:])
            return 2
        print(f"\n{len(findings)} finding(s) over {len(targets)} target(s):\n")
        for f in findings:
            print(json.dumps(f, indent=2, ensure_ascii=False))
        for k in skipped:
            print("SKIPPED (not checked, not clean): " + json.dumps(k, ensure_ascii=False))
        if not findings:
            print("(none)")
        return 0
    finally:
        sh("docker", "rm", "-f", CONTAINER_NAME, check=False)


GATE_CONTAINER = "lusterna-checklean-gate"


def run_gate_fixture() -> int:
    """Drive the REAL taint gate — `lean.target_footprint_gate` (build + `checkDefAxioms` + the
    block/pass decision + its categorised feedback + fail-closed handling) — over the Klendish
    fixture: a klend-shaped translation with a CLEAN target and a target that transitively reaches a
    `Display`/`fmt` opaque. The DefAx fixture covers the Lean checker; THIS covers the Python gate
    WRAPPER (the trusted verdict the TRANSLATE stage blocks on). Asserts BLOCK / PASS / fail-closed.
    Also validates `lean.impl_references` (the impl-verified partition) on the open'd-short-name case
    that a text scan got wrong."""
    sh("docker", "rm", "-f", GATE_CONTAINER, check=False)
    cid = sh("docker", "run", "--rm", "--detach", "--name", GATE_CONTAINER,
             "lusterna-toolchain:latest", "sleep", "infinity").strip()
    print(f"\n[gate fixture] container {cid[:12]}")
    try:
        deps = AgentDeps(container_id=cid, repo_path=HERE, session_id="gate",
                         design_doc="", campaign="Solvency")
        lean_dir = f"{container.OUT_IN}/lean"
        sh("docker", "exec", cid, "mkdir", "-p", lean_dir)
        translation = (HERE / "Klendish.lean").read_text()
        assert not tools.write_out(deps, "lean/KlendishModel.lean", translation).startswith("ERROR:")
        lean.setup_lake(deps)
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            print(f"gate fixture did not build:\n{out}\n{err}")
            return 1
        files, lp = ["lean/KlendishModel.lean"], "lean/KlendishModel.lean"
        deps.progress["aeneas"] = {"lean_path": lp, "lean_files": files, "holes": []}

        def gate(patterns: list[str]) -> dict:
            deps.progress["target_patterns"] = patterns
            return lean.target_footprint_gate(deps, translation, files, lp)

        # leaky+clean → BLOCK: only `repay` flagged, feedback categorises the Display leak.
        g1 = gate(["crate::_::total_supply", "crate::_::repay"])
        case1 = (not g1["ok"] and any("repay" in d for d in g1["footprint"])
                 and all("total_supply" not in d for d in g1["footprint"])
                 and "Display" in g1["feedback"])
        # clean-only → PASS (empty footprint).
        g2 = gate(["crate::_::total_supply"])
        case2 = g2["ok"] and not g2["footprint"]
        # nonexistent target → BLOCK, fail-closed (the vacuous false-clean a name mismatch produces).
        g3 = gate(["crate::_::does_not_exist"])
        case3 = (not g3["ok"]) and "matched NO target" in g3["feedback"]

        ok = True
        for name, passed in [("leaky → BLOCK", case1), ("clean → PASS", case2),
                             ("vacuous → fail-closed BLOCK", case3)]:
            print(f"  taint gate: {name}: {'PASS' if passed else 'FAIL'}")
            ok = ok and passed
        if not case1:
            print(f"    (leaky footprint={g1['footprint']}, ok={g1['ok']})")

        # ── impl_references: does a theorem VERIFY THE IMPLEMENTATION? ─────────────────────────────
        # The exact bug this replaced: `ts_uses_impl` references the translated `total_supply` via the
        # OPENED short name (`open klendish`), which a text scan of the full in-namespace def name
        # missed → every theorem wrongly "abstract". `pure_lemma` is genuinely abstract (Nat only).
        spec = ("import KlendishModel\nopen klendish\n"
                "namespace KlendishModel.Spec.Solvency\n"
                "theorem ts_uses_impl (x : Nat) (h : total_supply x = 1) : True := trivial\n"
                "theorem pure_lemma (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
                "end KlendishModel.Spec.Solvency\n")
        assert not tools.write_out(deps, "lean/KlendishModel/Spec/Solvency.lean", spec).startswith("ERROR:")
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            print(f"  impl_references: spec did not build:\n{out}\n{err}")
            ok = False
        else:
            refs = lean.impl_references(deps, "lean/KlendishModel/Spec/Solvency.lean")
            case4 = refs.get("ts_uses_impl") is True and refs.get("pure_lemma") is False
            print(f"  impl_references (open'd name → impl-verified, abstract → not): "
                  f"{'PASS' if case4 else 'FAIL'}")
            if not case4:
                print(f"    (got {refs})")
            ok = ok and case4
        return 0 if ok else 1
    finally:
        sh("docker", "rm", "-f", GATE_CONTAINER, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, help="an arbitrary .lean file to check (root module)")
    ap.add_argument("--targets", help="comma-separated fully-qualified theorem/def names to check")
    ap.add_argument("--deps", nargs="*", type=Path, default=[], help="extra .lean files the target imports")
    ap.add_argument("--subjects", help="comma-separated function names under test; enables the target-aware checks")
    ap.add_argument("--continuations", default="",
                    help="comma-separated functions a theorem may legitimately chain through")
    args = ap.parse_args()

    if not shutil_which("docker"):
        sys.exit("docker not on PATH")
    if args.file:
        if not args.targets:
            sys.exit("--targets is required with --file")
        return run_arbitrary(args.file, [t.strip() for t in args.targets.split(",")], args.deps,
                             [g.strip() for g in (args.subjects or "").split(",") if g.strip()],
                             [g.strip() for g in args.continuations.split(",") if g.strip()])
    # Two independent fixtures (separate containers — each wires its own lake lib): the spec-gate /
    # checker regression, and the taint-gate wrapper. Both must pass.
    rc = run_fixture()
    rc = run_gate_fixture() or rc
    return rc


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


if __name__ == "__main__":
    raise SystemExit(main())
