"""Pipeline stages: one Agent per stage, Python orchestrates sequencing and loops.

Each stage agent runs independently with no shared message history — stages
communicate via the filesystem and deps.progress, not via conversation context.
Embedded specialists (subagents.py) are invoked as tool calls from within a
stage and return structured data directly to the calling stage.
"""
import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from . import checkpoint, docs, factory, git_ops, subagents, telemetry, tools
from .schemas import (
    AbstractInformalSpec, AbstractFormalSpec,
    InformalSpec, JudgeVerdict, ProofVerdict, ReconciliationReport,
    TheoremEstimate,
)
from .state import AgentDeps
from . import config

log = logging.getLogger(__name__)


def run_aeneas(ctx: RunContext[AgentDeps], entry_file: str) -> dict:
    """Translate *entry_file* (repo-relative) to Lean 4 via Charon + Aeneas.
    On success saves the result to progress and checkpoints. Inspect aeneas_errors /
    charon_errors, fix with write_rust_file, then retry (max 2 times)."""
    result = tools.run_aeneas(ctx.deps, entry_file)
    if result.get("success") or result.get("lean_files"):
        ctx.deps.progress["aeneas"] = result
        _checkpoint(ctx.deps)
    return result



def check_lean(ctx: RunContext[AgentDeps], lean_file: str) -> dict:
    """Run `lake build` in the Lean project and return {success, stderr}.

    The result is stored in progress['lean_build'] and a checkpoint is saved.
    Always call this after writing or modifying any Lean file.
    """
    result = tools.check_lean(ctx.deps, lean_file)
    ctx.deps.progress["lean_build"] = result
    _checkpoint(ctx.deps)
    return result


async def _run_inline_judge(deps: AgentDeps) -> ProofVerdict | None:
    """Run _proof_judge from inside a tool call (does not reset stage telemetry).

    Stores the verdict in deps.progress['proof_verdict'] on success.
    """
    from pydantic_ai.usage import UsageLimits
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    spec_path = lean_path.replace(".lean", "Spec.lean") if lean_path else ""
    spec_content = tools.read_out(deps, spec_path) if spec_path else ""
    build = deps.progress.get("lean_build", {})
    build_line = f"lake build {'passed ✓' if build.get('success') else 'FAILED ✗'}"
    if not build.get("success") and build.get("stderr"):
        build_line += f"\n{build['stderr']}"
    prompt = (
        _pipeline_briefing(deps)
        + f"Inline PROOF-JUDGE: classify every theorem as proved / sorry_acceptable / "
          f"likely_misstated.\n\n{build_line}\n\n### {spec_path}\n{spec_content}"
    )
    try:
        async with _proof_judge.iter(
            prompt, deps=deps,
            usage_limits=UsageLimits(request_limit=config.REQUEST_LIMIT),
        ) as run:
            async for _ in run:
                pass
            result = run.result
    except UnexpectedModelBehavior as e:
        log.warning("Inline proof-judge failed: %s", e)
        return None
    if not (result and result.output):
        return None
    pv: ProofVerdict = result.output
    deps.progress["proof_verdict"] = pv.model_dump()
    _checkpoint(deps)
    log.info(
        "Inline proof-judge: proved=%d sorry=%d misstated=%d stagnant=%s",
        sum(1 for t in pv.theorems if t.status == "proved"),
        sum(1 for t in pv.theorems if t.status == "sorry_acceptable"),
        sum(1 for t in pv.theorems if t.status == "likely_misstated"),
        pv.stagnant,
    )
    return pv


async def check_and_judge(ctx: RunContext[AgentDeps], lean_file: str) -> dict:
    """Run `lake build` then immediately run an embedded PROOF-JUDGE on success.

    Use this (not check_lean) when proving theorems. The result includes a
    'proof_judge' key on successful builds. Inspect it after every call:
      - proof_judge.stagnant == true  →  call git_commit and stop immediately.
      - proof_judge.likely_misstated  →  note names but do NOT alter statements.
      - No sorry theorems remaining   →  call git_commit and stop.
    Always call check_and_judge as your last action before git_commit.
    """
    result = tools.check_lean(ctx.deps, lean_file)
    ctx.deps.progress["lean_build"] = result
    _checkpoint(ctx.deps)
    if result["success"]:
        pv = await _run_inline_judge(ctx.deps)
        if pv:
            result["proof_judge"] = {
                "stagnant": pv.stagnant,
                "proved": [t.name for t in pv.theorems if t.status == "proved"],
                "sorry_acceptable": [t.name for t in pv.theorems if t.status == "sorry_acceptable"],
                "likely_misstated": [t.name for t in pv.theorems if t.status == "likely_misstated"],
            }
    return result


# ── pipeline stages ───────────────────────────────────────────────────────────

_doc_infer = factory.make_stage_agent("""
You are the DOC-INFER stage of the Lusterna pipeline.

Derive an abstract informal specification from the design document ALONE.
You have NO access to the Rust source code or the Lean translation.

The full design document is provided in your prompt. Read it carefully and return
a structured AbstractInformalSpec:
  - preconditions: what must hold before the system is called
  - postconditions: what the system guarantees on return
  - invariants: properties that must hold throughout execution
  - edge_cases: boundary conditions, overflow, empty input, etc.
  - open_questions: aspects the design document does not specify

Be precise and concise. Do not invent behaviour not evidenced by the document.
""",
    output_type=AbstractInformalSpec,
    retries=2,
)


_doc_formalise = factory.make_stage_agent("""
You are the DOC-FORMALISE stage of the Lusterna pipeline.

Produce a Lean 4 abstract formal specification from the abstract informal spec.
You have NO access to the Rust source code or the Lean translation.

The abstract informal specification is provided in your prompt. From it, derive:
  - lean_definitions: abstract type definitions and predicates (no Rust types)
  - lean_theorem_stubs: theorem statements with sorry, referencing only abstract types
  - rationale: brief explanation of the modelling choices
  - ambiguities: list any aspects of the informal spec that are too ambiguous to
    formalise faithfully (field name + question); leave empty when the spec is clear.

Use abstract mathematical types (Nat, List, Set, etc.). Each theorem stub needs a docstring.
""",
    output_type=AbstractFormalSpec,
    retries=2,
)


_explore = factory.make_stage_agent("""
You are the EXPLORE stage of the Lusterna formal verification pipeline.

Understand the Rust codebase before translation begins. The goal is formal
verification via Aeneas (Rust→Lean 4) and Lean 4 proof assistants — flag anything
that would prevent or degrade an Aeneas translation.

- List all .rs and .toml files.
- Read the key source files (lib.rs, main.rs, Cargo.toml).
- Identify functions to be translated and flag obvious Aeneas incompatibilities
  (vec!, println!, trait objects, unsupported std types, etc.).

Write a short structured summary of findings. Then stop — do not run Aeneas.
""",
)
_explore.tool(tools.list_files)
_explore.tool(tools.read_file)


_translate = factory.make_stage_agent("""
You are the TRANSLATE stage of the Lusterna pipeline.

Translate the Rust codebase to Lean 4 via Charon + Aeneas:

1. Call run_aeneas with the main entry file ('src/lib.rs' or 'src/main.rs').
2. Interpret the result:
   a. success=true  → done, commit and report.
   b. partial=true or charon_errors non-empty → read errors carefully.
      Common fixes:
        - vec!/println!/eprintln! in main → rewrite src/main.rs, stubbing out main body.
        - Unsupported alloc/std constructs → replace with stubs or remove.
      Call write_rust_file to apply the fix, then call run_aeneas again (max 2 retries).
      Accept partial output if not all errors are fixable.
   c. success=false AND lean_files=[] → try fixing Rust (max 2 retries); if still nothing,
      report failure and stop.
3. Commit the Lean output once translation produces at least some files.

When inspecting Lean output files, prefer search_output_file + read_output_lines over
read_output_file to avoid loading entire files unnecessarily.
""" + docs.FOR_TRANSLATE,
)
_translate.tool(tools.list_files)
_translate.tool(tools.read_file)
_translate.tool(tools.read_output_file)
_translate.tool(tools.search_output_file)
_translate.tool(tools.read_output_lines)
_translate.tool(tools.write_rust_file)
_translate.tool(run_aeneas)
_translate.tool(tools.git_commit)
_translate.tool(tools.git_log)


_infer = factory.make_stage_agent("""
You are the INFER stage of the Lusterna pipeline.

Derive an informal specification from the Aeneas-translated Lean output and the design
document. Both are provided directly in your prompt — do not call any tools.

Return a structured InformalSpec: preconditions, postconditions, invariants, edge cases.
Be precise and concise. Do not invent behaviour not evidenced by the code or the design
document. Where the abstract informal spec (if present) covers the same aspect, align
with its structure.
""",
    output_type=InformalSpec,
    retries=2,
)


_formalise = factory.make_stage_agent("""
You are the FORMALISE+BUILD stage of the Lusterna pipeline.

Produce a Lean 4 formal specification that compiles with `lake build`.
Write theorem stubs only — use `sorry` for all proofs. Do NOT attempt proofs.

1. If specs/abstract_formal_spec.lean exists (listed in the pipeline context), read it
   first — it is the ABSTRACT specification derived from the design document alone and
   defines the theorems the implementation must satisfy. Use it as a guide for which
   theorems to include; the implementation spec should cover at least these obligations.

2. Read specs/informal_spec.json (listed in the pipeline context). Derive Lean 4
   theorem stubs from it. Write two files:
     - specs/formal_spec.lean — definitions and theorem stubs (all sorry)
     - lean/<CrateName>Spec.lean — the same stubs, importing the Aeneas translation
   Make sure lean/lakefile.lean declares lean/<CrateName>Spec.lean as a lean_lib target.

4. Call check_lean to run `lake build`.

5. If the build fails:
   - Read stdout/stderr carefully.
   - Fix type errors, missing imports, namespace issues. For targeted fixes use
     search_output_file to locate the relevant lines, then patch_output_lines to
     replace only those lines. Use write_file only to create or fully replace a file.
   - Call check_lean again. Repeat up to 3 total build attempts.

6. Commit everything once the build passes (or after all attempts, noting any failures).

Do NOT attempt proofs — that is the PROVE stage's responsibility.
""" + docs.FOR_FORMALISE,
)
_formalise.tool(tools.list_files)
_formalise.tool(tools.read_file)
_formalise.tool(tools.read_output_file)
_formalise.tool(tools.search_output_file)
_formalise.tool(tools.read_output_lines)
_formalise.tool(tools.patch_output_lines)
_formalise.tool(tools.write_file)
_formalise.tool(check_lean)
_formalise.tool(tools.git_commit)
_formalise.tool(tools.git_log)


_judge = factory.make_stage_agent("""
You are the JUDGE stage of the Lusterna pipeline.

Evaluate the formal Lean 4 specification and return a structured JudgeVerdict that
includes both an overall verdict and a per-component breakdown.

All files you need are injected directly into your prompt — do not call any tools.
The build result, implementation spec, abstract spec, and informal spec are all provided.

specs/abstract_formal_spec.lean is the ABSTRACT specification derived from the design
document alone, with no knowledge of the Rust implementation. Use it as a ground-truth
reference: if a theorem in the implementation spec contradicts or is weaker than the
abstract spec, flag it as a critical issue. Gaps in the abstract spec are acceptable.

For EACH theorem, definition, and lemma in the spec file, produce a ComponentVerdict:
  - name: the Lean identifier (e.g. "fib_recursive_correct")
  - kind: "theorem" | "definition" | "lemma" | "other"
  - approved: true only if the statement is sound and complete for its purpose
  - score: 0-10 for this component
  - issues: specific problems (wrong quantifier, missing edge case, unsound statement…)
  - suggestions: concrete fixes the formaliser should apply

Then produce the overall JudgeVerdict:
  - approved: true only if lake build passed AND all critical components are approved
  - score: 0-10 weighted average across components
  - issues: cross-cutting problems not tied to one component
  - suggestions: overall structural improvements
  - components: the list of ComponentVerdicts above

STAGNATION FIELD — read this carefully before setting stagnant:

  Set stagnant=true ONLY when ALL THREE of the following hold simultaneously:
    1. This is not the first judging round (a prior spec-judge verdict is shown in the
       pipeline context at the top of this prompt under "Spec-judge verdict").
    2. Every component that was failing in the previous round is still failing now,
       AND no previously-failing component has been removed or replaced.
    3. The Lean theorem/definition statements for those failing components are
       materially unchanged from the previous round — not just similar in meaning,
       but the same logical content and structure. Minor renaming or reformatting
       does NOT count as progress; fixing even one substantive issue in any failing
       component DOES count as progress.

  Default to stagnant=false. Only set stagnant=true when you are certain the
  formaliser has made zero substantive progress on the failing components.
  When in doubt, set stagnant=false and let another round proceed.

IMPORTANT: if lake build failed, approved MUST be false and score MUST be ≤ 4.
""",
    output_type=JudgeVerdict,
    retries=3,
)


_reconcile = factory.make_stage_agent("""
You are the RECONCILE stage of the Lusterna pipeline.

Compare the abstract formal specification (derived from the design document alone)
against the implementation formal specification (derived from the Aeneas translation).
Return a structured ReconciliationReport.

All files you need are injected directly into your prompt — do not call any tools.
specs/abstract_formal_spec.lean is the ABSTRACT spec — produced with zero knowledge
of the Rust source; it represents design intent.
lean/*Spec.lean is the IMPLEMENTATION spec, derived from the Aeneas translation.

For each theorem/definition, determine whether the two specs agree, diverge, or
whether one side is simply silent.

Classify each discrepancy with one of four kinds:

  "implementation_wrong"  — CRITICAL. The impl spec reveals that the Rust code
      behaves differently from the design intent described in the abstract spec.
      Example: abstract spec says output is always positive; impl spec has no such
      guarantee because the code can return 0.

  "bridge_wrong"          — CRITICAL. The impl spec was incorrectly derived: the
      Aeneas translation is correct but the agent mis-stated a theorem so that it
      no longer captures what the code actually does. The design intent and the code
      may both be fine, but the impl spec is wrong.

  "abstract_wrong"        — The abstract model misreads or over-specifies the design
      document. The implementation and its spec are correct; the abstract model needs
      revision.

  "design_doc_silent"     — The design document simply did not cover this aspect.
      The impl spec adds detail that the abstract spec cannot contradict. This is an
      acceptable gap, not a discrepancy.

Severity:
  "critical" for implementation_wrong and bridge_wrong
  "minor"    for abstract_wrong
  "gap"      for design_doc_silent

For each critical discrepancy, produce a RefinementObligation: a Lean 4 theorem stub
(with sorry) whose proof would formally bridge the impl spec to the abstract spec, or
whose unprovability would confirm the discrepancy. Name it clearly (e.g.
"fib_impl_refines_abstract_correctness").

List in aligned[] the names of impl-spec components that cleanly satisfy the
corresponding abstract-spec requirement with no discrepancy.

IMPORTANT: design_doc_silent gaps are NOT discrepancies — do not list them unless
you also want to generate a refinement obligation for them. When in doubt about
whether something is a gap or a real discrepancy, classify it as design_doc_silent.
""",
    output_type=ReconciliationReport,
    retries=3,
)


_prove = factory.make_stage_agent("""
You are the PROVE stage of the Lusterna pipeline.

The formal spec has passed the spec-judge threshold (score ≥ 7 or approved). Your
job is to attempt to prove as many theorems and lemmas as possible using Lean 4
tactics, without changing any theorem or definition statements.

An EFFORT ESTIMATE is provided in the runtime prompt. Follow it strictly:
  trivial / moderate  →  attempt these; spend up to 2-3 tactic tries each
  hard_acceptable     →  leave as sorry immediately without any proof attempt
  likely_misstated    →  do NOT touch; note the name in your commit message

Workflow — work ONE theorem at a time to keep context small:
1. Call list_files('lean') to find spec files.
2. Call search_output_file(file, 'theorem|lemma') to list all theorem/lemma names
   with their line numbers.
3. For each sorry theorem classified trivial or moderate, in order:
   a. Call search_output_file to locate it precisely, then read_output_lines to fetch
      just that theorem block (from its `theorem` line to its `:= by sorry` line).
   b. Attempt tactics in this order: rfl, simp, omega, norm_num, decide,
      native_decide, ring, linarith, then induction/cases with sub-goal tactics.
   c. When you have a candidate proof, call patch_output_lines to replace ONLY the
      proof body (the lines from `:= by` to the closing `sorry`) — do not touch
      anything outside that range.
   d. Call check_and_judge. If the build fails, call patch_output_lines again to
      revert that theorem to `sorry` (restore the exact original lines), then move on.
4. After attempting all tractable theorems, call check_and_judge one final time,
   then call git_commit and stop.

After each SUCCESSFUL check_and_judge the result contains a 'proof_judge' section.
Inspect it immediately:
  - proof_judge.stagnant == true  →  call git_commit and stop now.
  - proof_judge.likely_misstated  →  note those names; do NOT alter their statements.
  - All theorems proved or sorry_acceptable  →  call git_commit and stop now.

STRICT RULES:
- NEVER alter a theorem's statement (the part before `:= by`).
- NEVER use write_file or append_file to replace a whole spec file — use
  patch_output_lines for targeted edits and read_output_lines to inspect context.
  write_file is allowed ONLY for creating brand-new files (e.g. reconciliation stubs).
- NEVER introduce an axiom or `#check` that weakens the spec.
- If a proof takes more than 2-3 tactic attempts, leave it as `sorry` and move on.
- It is acceptable — even expected — to leave hard theorems as `sorry`.
""" + docs.FOR_PROVE,
)
_prove.tool(tools.list_files)
_prove.tool(tools.search_output_file)
_prove.tool(tools.read_output_lines)
_prove.tool(tools.read_output_file)
_prove.tool(tools.patch_output_lines)
_prove.tool(tools.write_file)
_prove.tool(check_and_judge)
_prove.tool(tools.git_commit)
_prove.tool(tools.git_log)


_proof_judge = factory.make_stage_agent("""
You are the PROOF JUDGE stage of the Lusterna pipeline.

Evaluate every theorem and lemma in the formal spec and return a ProofVerdict.
The current spec file content and build result are injected directly in your prompt —
do not call any tools.

For each component classify its status:
  "proved"            — proof is complete (no sorry), compiles, and is correct
  "sorry_acceptable"  — theorem is correctly stated but requires advanced techniques
                        beyond automation (deep induction, non-trivial Mathlib lemmas,
                        novel mathematical arguments). sorry is the right placeholder.
  "likely_misstated"  — the theorem CANNOT be proved as stated because the statement
                        itself is logically wrong: wrong quantifier, wrong bound,
                        inconsistent precondition, output type mismatch, etc.

CRITICAL — only use "likely_misstated" when you can state a SPECIFIC logical reason:
  ✓ "Precondition `n < 100` should be `n ≤ 93` — UInt64 overflows at fib(94)"
  ✓ "Postcondition equates UInt64 and Nat directly; needs a cast or modular equivalence"
  ✗ "I could not find a proof" — this is sorry_acceptable, not likely_misstated
  ✗ "The proof is complex" — same, sorry_acceptable
  When in doubt, classify as sorry_acceptable.

STAGNATION — set stagnant=true ONLY when ALL of:
  1. This is not the first proof-judge round (a prior proof-judge verdict is shown in
     the pipeline context at the top of this prompt under "Proof-judge verdict").
  2. The set of likely_misstated theorems is identical to the previous round.
  3. The proof automator made no changes to those theorems' statements or proof attempts.
  Default to stagnant=false.
""",
    output_type=ProofVerdict,
    retries=3,
)


_report = factory.make_stage_agent("""
You are the REPORT stage of the Lusterna formal verification pipeline.

All artefacts you need are injected directly in your prompt — do not call any read tools.

Write each section as a SEPARATE FILE under report/ using write_file once per section.
The calling code concatenates them into VERIFICATION_REPORT.md — do NOT write that
file yourself and do NOT call git_commit.

Write exactly these files, in this order:

  report/01_overview.md
      Title, one-paragraph executive summary, overview table (translation result,
      spec-judge score, proved/sorry/misstated counts, critical discrepancies).

  report/02_translation.md
      What was translated: entry file, Rust changes required (stubs, removals),
      Aeneas output files, any partial-translation caveats.

  report/03_abstract_spec.md
      List the key theorems and definitions from the abstract spec with a one-line
      gloss for each. Note any open_questions the doc-inferrer flagged.

  report/04_implementation_spec.md
      List every theorem stub from the implementation spec with its statement and a
      one-line explanation. Include the lake build result.

  report/05_spec_judge.md
      Spec-judge verdict: overall score, approved/not, per-component breakdown.
      Quote the judge's issues and suggestions for any component scoring < 8.
      (Verdict data is in the pipeline context above.)

  report/06_reconciliation.md
      For each reconciliation cycle: aligned components, discrepancies (with kind,
      severity, description), refinement obligations. Flag any discrepancy that
      appeared in an earlier cycle but vanished later — that is suspicious.

  report/07_proofs.md
      Proof-judge verdict: per-theorem classification (proved/sorry_acceptable/
      likely_misstated), proof sketch for each proved theorem, suggested strategy
      for each sorry_acceptable, precise reason for any likely_misstated.
      (Verdict data is in the pipeline context above.)

  report/08_summary.md
      Open proof obligations (each sorry with a concrete next step), known gaps
      and limitations, overall verdict paragraph.

Be thorough — do not summarise away detail that would help a reader understand
what was verified, what was found, and what remains open.
""",
)
_report.tool(tools.write_file)
_report.tool(tools.git_log)


# ── internal helpers ──────────────────────────────────────────────────────────

def _checkpoint(deps: AgentDeps) -> None:
    checkpoint.save(
        deps.session_id,
        {
            "repo_path": str(deps.repo_path),
            "work_path": str(deps.work_path),
            "container_id": deps.container_id,
            "design_doc": deps.design_doc,
            "progress": deps.progress,
            "git_head": git_ops.head_sha(deps.container_id),
        },
    )


class _PipelineAborted(Exception):
    pass


_HARD_CAP  = 10   # max spec-judge rounds per cycle
_CYCLE_CAP = 5    # max full spec→proof cycles


def _pipeline_briefing(deps: AgentDeps) -> str:
    """Return a structured context block describing pipeline state so far.

    Prepended to every stage's runtime prompt so each stage agent understands
    the overall goal, what has been completed, and key findings from prior stages.
    """
    p = deps.progress

    # Completed stages
    stage_flags = [
        ("abstract_informal_spec", "DOC-INFER"),
        ("abstract_formal_spec",   "DOC-FORMALISE"),
        ("aeneas",                 "EXPLORE+TRANSLATE"),
        ("informal_spec",          "INFER"),
        ("formal_spec",            "FORMALISE"),
        ("verdict",                "SPEC-JUDGE"),
        ("reconciliation_history", "RECONCILE"),
        ("proof_verdict",          "PROOF-JUDGE"),
    ]
    done = [label for key, label in stage_flags if key in p]

    lines = [
        "## Pipeline context",
        f"Goal: formally verify the Rust crate against the design document.",
        f"Design document (excerpt):\n{deps.design_doc[:600].rstrip()}",
        "",
        f"Completed stages: {', '.join(done) if done else 'none yet'}",
    ]

    # Artefact inventory
    artefacts = []
    if "aeneas" in p:
        lean_path = p["aeneas"].get("lean_path", "")
        if lean_path:
            artefacts.append(lean_path)
    for key, path in [
        ("abstract_informal_spec", "specs/abstract_informal_spec.json"),
        ("abstract_formal_spec",   "specs/abstract_formal_spec.lean"),
        ("informal_spec",          "specs/informal_spec.json"),
        ("formal_spec",            "specs/formal_spec.lean"),
    ]:
        if key in p:
            artefacts.append(path)
    rc_history = p.get("reconciliation_history", [])
    for i, _ in enumerate(rc_history):
        artefacts.append(f"specs/reconciliation_cycle_{i + 1}.json")
    if artefacts:
        lines.append(
            f"Key artefacts (all in /workspace/out — use read_output_file): "
            + ", ".join(artefacts)
        )

    # Spec-judge verdict
    verdict = p.get("verdict")
    if verdict:
        approved = verdict.get("approved", False)
        score = verdict.get("score", "?")
        issues = verdict.get("issues", [])
        comps = verdict.get("components", [])
        failing = [c["name"] for c in comps if not c.get("approved")]
        lines.append(
            f"\nSpec-judge verdict: {'approved ✓' if approved else 'not approved ✗'} "
            f"score={score}"
            + (f"  failing={failing}" if failing else "")
            + (f"  issues={issues[:2]}" if issues else "")
        )

    # Reconciliation summary (all cycles)
    if rc_history:
        lines.append(f"\nReconciliation ({len(rc_history)} cycle(s)):")
        for rc_entry in rc_history:
            cyc = rc_entry.get("cycle", "?")
            discrepancies = rc_entry.get("discrepancies", [])
            critical = [d for d in discrepancies if d.get("severity") == "critical"]
            obligations = rc_entry.get("refinement_obligations", [])
            lines.append(
                f"  Cycle {cyc}: {len(discrepancies)} discrepancy/ies "
                f"({len(critical)} critical), {len(obligations)} refinement obligation(s)"
            )
            for d in critical:
                lines.append(f"    [CRITICAL {d.get('kind','')}] {d.get('description','')[:120]}")

    # Proof-judge verdict
    proof_verdict = p.get("proof_verdict")
    if proof_verdict:
        theorems = proof_verdict.get("theorems", [])
        proved   = [t["name"] for t in theorems if t.get("status") == "proved"]
        sorry    = [t["name"] for t in theorems if t.get("status") == "sorry_acceptable"]
        mis      = [t["name"] for t in theorems if t.get("status") == "likely_misstated"]
        lines.append(
            f"\nProof-judge verdict: proved={len(proved)} sorry_acceptable={len(sorry)} "
            f"likely_misstated={len(mis)}"
            + (f"  misstated={mis}" if mis else "")
        )

    # Effort estimate (survives compaction — re-injected into every briefing)
    effort = p.get("effort_estimate")
    if effort:
        def _ns(lst): return ", ".join(lst) if lst else "none"
        lines.append(
            f"\nEffort estimate (PROVE guidance):"
            f"\n  trivial (attempt):               {_ns(effort.get('trivial', []))}"
            f"\n  moderate (attempt):              {_ns(effort.get('moderate', []))}"
            f"\n  hard_acceptable (leave sorry):   {_ns(effort.get('hard_acceptable', []))}"
            f"\n  likely_misstated (do not touch): {_ns(effort.get('likely_misstated', []))}"
            f"\nAttempt only trivial and moderate. Mark hard_acceptable as sorry immediately."
        )

    lines.append("")   # trailing newline before stage-specific prompt
    return "\n".join(lines) + "\n"


async def _run_stage(agent: Agent, prompt: str, deps: AgentDeps, label: str) -> Any:
    """Run one stage agent to completion. Each stage starts with no prior history.

    Stages communicate via the filesystem and deps.progress, not via conversation
    context — so no history is passed in or accumulated across stages.
    Re-raises UnexpectedModelBehavior; judge stages catch it locally.
    """
    log.info("─── Stage: %s ───", label)
    telemetry.stage.reset()
    deps.message_history = []
    full_prompt = _pipeline_briefing(deps) + prompt
    from pydantic_ai.usage import UsageLimits
    async with agent.iter(
        full_prompt, deps=deps,
        usage_limits=UsageLimits(request_limit=config.REQUEST_LIMIT),
    ) as run:
        async for _node in run:
            if deps.progress.get("proof_verdict", {}).get("stagnant"):
                break
        result = run.result
    log.info(
        "Stage %s complete — session total=%d/%s",
        label, telemetry.session.total(), telemetry.budget or "∞",
    )
    return result


# ── pipeline sub-functions ────────────────────────────────────────────────────

async def _run_doc_stages(deps: AgentDeps, resume_note: str) -> str:
    """Run DOC-INFER and DOC-FORMALISE with an orchestrator-level ambiguity loop."""
    completed = set(deps.progress.keys())

    _MAX_DOC_ROUNDS = 3

    async def _run_doc_infer(prompt_suffix: str = "") -> AbstractInformalSpec | None:
        result = await _run_stage(
            _doc_infer,
            f"Begin DOC-INFER. Derive the abstract informal specification from this "
            f"design document:\n\n{deps.design_doc}" + prompt_suffix,
            deps, "DOC-INFER",
        )
        if result and result.output:
            spec: AbstractInformalSpec = result.output
            deps.progress["abstract_informal_spec"] = spec.model_dump()
            tools.write_out(deps, "specs/abstract_informal_spec.json", spec.model_dump_json(indent=2))
            return spec
        return None

    if "abstract_informal_spec" not in completed:
        informal = await _run_doc_infer(resume_note)
        _checkpoint(deps)
        resume_note = ""
        if informal is None:
            log.warning("DOC-INFER produced no output — proceeding without abstract spec")
            return resume_note
    else:
        informal = AbstractInformalSpec(**deps.progress["abstract_informal_spec"])

    if "abstract_formal_spec" in completed:
        return resume_note

    for round_num in range(_MAX_DOC_ROUNDS):
        inf_json = AbstractInformalSpec(**deps.progress["abstract_informal_spec"]).model_dump_json(indent=2)
        formalise_result = await _run_stage(
            _doc_formalise,
            f"DOC-FORMALISE (round {round_num + 1}). Produce Lean 4 abstract theorem stubs "
            f"from this abstract informal spec:\n\n{inf_json}" + resume_note,
            deps, f"DOC-FORMALISE (round {round_num + 1})",
        )
        resume_note = ""

        if not (formalise_result and formalise_result.output):
            log.warning("DOC-FORMALISE round %d produced no output", round_num + 1)
            break

        formal: AbstractFormalSpec = formalise_result.output
        deps.progress["abstract_formal_spec"] = formal.model_dump()
        tools.write_out(
            deps, "specs/abstract_formal_spec.lean",
            formal.lean_definitions + "\n\n" + formal.lean_theorem_stubs,
        )
        git_ops.commit(deps.container_id, "feat(spec): abstract formal specification", glob="specs/")
        _checkpoint(deps)

        if not formal.ambiguities or round_num == _MAX_DOC_ROUNDS - 1:
            break

        log.info(
            "DOC-FORMALISE flagged %d ambiguity/ies — re-running DOC-INFER (round %d/%d)",
            len(formal.ambiguities), round_num + 1, _MAX_DOC_ROUNDS,
        )
        ambiguity_lines = "\n".join(f"  - {a.field}: {a.question}" for a in formal.ambiguities)
        del deps.progress["abstract_informal_spec"]   # allow re-run
        informal = await _run_doc_infer(
            f"\n\nThe doc-formaliser flagged these ambiguities — resolve them using "
            f"only the design document:\n{ambiguity_lines}"
        )
        _checkpoint(deps)
        if informal is None:
            break

    return resume_note


async def _run_translate_stages(deps: AgentDeps, resume_note: str) -> str:
    """Run EXPLORE, TRANSLATE, and INFER (skipped if already in progress).

    Raises _PipelineAborted if TRANSLATE or INFER produce no output.
    """
    completed = set(deps.progress.keys())

    if "aeneas" not in completed:
        await _run_stage(
            _explore,
            f"Begin EXPLORE for the Rust repository at {deps.repo_path}.\n"
            f"Design document:\n{deps.design_doc[:2000]}" + resume_note,
            deps, "EXPLORE",
        )
        _checkpoint(deps)
        resume_note = ""

    if "aeneas" not in completed:
        await _run_stage(
            _translate,
            "Proceed to TRANSLATE. Run Aeneas on the Rust source; fix any "
            "Charon/Aeneas errors by massaging the Rust source as needed." + resume_note,
            deps, "TRANSLATE",
        )
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "aeneas" not in completed:
            raise _PipelineAborted("Aeneas translation failed after retries.")

    if "informal_spec" not in completed:
        lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
        lean_content = tools.read_out(deps, lean_path) if lean_path else ""
        abstract_informal = tools.read_out(deps, "specs/abstract_informal_spec.json")
        infer_files = f"### {lean_path}\n{lean_content}"
        if not abstract_informal.startswith("ERROR:"):
            infer_files += f"\n\n### specs/abstract_informal_spec.json\n{abstract_informal}"
        infer_result = await _run_stage(
            _infer,
            "Proceed to INFER. Derive a structured InformalSpec from the following files:"
            f"\n\n{infer_files}" + resume_note,
            deps, "INFER",
        )
        if infer_result and infer_result.output:
            spec: InformalSpec = infer_result.output
            deps.progress["informal_spec"] = spec.model_dump()
            tools.write_out(deps, "specs/informal_spec.json", spec.model_dump_json(indent=2))
            git_ops.commit(deps.container_id, "feat(spec): informal specification", glob="specs/")
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "informal_spec" not in completed:
            raise _PipelineAborted("Informal spec inference failed.")

    return resume_note


def _read_spec_files(deps: AgentDeps) -> str:
    """Read all spec files needed by judge and reconcile stages and return as a formatted block."""
    paths = [
        "specs/abstract_formal_spec.lean",
        "specs/abstract_informal_spec.json",
        "specs/informal_spec.json",
        "specs/formal_spec.lean",
    ]
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    if lean_path:
        paths.append(lean_path.replace(".lean", "Spec.lean"))
    parts = []
    for path in paths:
        content = tools.read_out(deps, path)
        if not content.startswith("ERROR:"):
            parts.append(f"### {path}\n{content}")
    return "\n\n".join(parts)


async def _run_spec_phase(
    deps: AgentDeps,
    cycle: int,
    resume_note: str,
    proof_amendments: list[dict],
) -> str:
    """Run the FORMALISE+SPEC-JUDGE loop for one cycle."""
    completed = set(deps.progress.keys())

    if "verdict" in completed and not proof_amendments:
        return resume_note

    spec_attempt = 0
    while spec_attempt < _HARD_CAP:
        skip_formalise = (
            spec_attempt == 0
            and cycle == 0
            and not proof_amendments
            and "formal_spec" in completed
            and deps.progress.get("lean_build", {}).get("success")
        )

        if not skip_formalise:
            if proof_amendments:
                amendment_lines = "\n".join(
                    f"  - {t['name']} ({t['kind']}): {t['misstatement_reason']}"
                    for t in proof_amendments
                )
                formalise_prompt = (
                    f"Cycle {cycle + 1}: the proof judge found the following "
                    f"{len(proof_amendments)} theorem(s) to be likely mis-stated. "
                    "Amend ONLY these statements — do not change any other component:\n"
                    f"{amendment_lines}\n\n"
                    "After editing, call check_lean to confirm the build still passes."
                )
                proof_amendments = []   # consumed
            elif spec_attempt == 0:
                formalise_prompt = (
                    "Proceed to FORMALISE+BUILD. Read specs/informal_spec.json, "
                    "derive Lean 4 theorem stubs (all sorry), write specs/formal_spec.lean "
                    "and lean/<CrateName>Spec.lean, register it in the lakefile, then call "
                    "check_lean until the build passes (max 3 build attempts)." + resume_note
                )
            else:
                failing_comps = [
                    c for c in deps.progress.get("verdict", {}).get("components", [])
                    if not c.get("approved")
                ]
                if failing_comps:
                    component_lines = "\n".join(
                        f"  - {c['name']} ({c['kind']}): "
                        + ("; ".join(c.get("issues", [])) or "no details")
                        for c in failing_comps
                    )
                    formalise_prompt = (
                        f"Spec judge did not approve (round {spec_attempt + 1}). "
                        f"Fix only these {len(failing_comps)} component(s):\n"
                        f"{component_lines}\n\n"
                        "Then call check_lean to confirm the build still passes."
                    )
                else:
                    formalise_prompt = (
                        f"Spec judge did not approve (round {spec_attempt + 1}). "
                        "Revise the spec based on the spec-judge verdict in the "
                        "pipeline context above, then call check_lean to confirm "
                        "the build passes."
                    )

            await _run_stage(
                _formalise, formalise_prompt, deps,
                f"FORMALISE (cycle {cycle + 1}, round {spec_attempt + 1})",
            )
            deps.progress["formal_spec"] = True
            _checkpoint(deps)
            resume_note = ""

        build_ok = deps.progress.get("lean_build", {}).get("success", False)
        build_stderr = deps.progress.get("lean_build", {}).get("stderr", "")
        spec_files = _read_spec_files(deps)
        try:
            sj_result = await _run_stage(
                _judge,
                f"SPEC-JUDGE: lake build {'passed ✓' if build_ok else 'FAILED ✗ — approved must be false'}."
                + (f"\nBuild errors:\n{build_stderr}" if not build_ok and build_stderr else "")
                + f"\n\nEvaluate theorem statements only (ignore sorry proofs).\n\n{spec_files}",
                deps,
                f"SPEC-JUDGE (cycle {cycle + 1}, round {spec_attempt + 1})",
            )
        except UnexpectedModelBehavior as e:
            log.warning("Spec judge failed after retries: %s — stopping spec loop", e)
            break

        if not (sj_result and sj_result.output):
            log.warning("Spec judge produced no output — stopping spec loop")
            break

        sv: JudgeVerdict = sj_result.output
        deps.progress["verdict"] = sv.model_dump()
        failing_names = sorted(c.name for c in sv.components if not c.approved)
        log.info(
            "Spec-judge cycle %d round %d: approved=%s score=%d "
            "components=%d failing=%s stagnant=%s",
            cycle + 1, spec_attempt + 1, sv.approved, sv.score,
            len(sv.components), failing_names or "none", sv.stagnant,
        )
        _checkpoint(deps)

        if sv.approved or sv.score >= 7:
            log.info("Spec verdict accepted")
            break
        if sv.stagnant:
            log.warning("Spec judge reports stagnation — exiting spec loop")
            break
        spec_attempt += 1
    else:
        log.warning("Spec-judge loop hit hard cap of %d rounds", _HARD_CAP)

    return resume_note


async def _run_reconcile_phase(deps: AgentDeps, cycle: int) -> None:
    """Run RECONCILE for one cycle (skipped if already done or no abstract spec)."""
    completed = set(deps.progress.keys())
    rc_history: list[dict] = deps.progress.setdefault("reconciliation_history", [])

    if "abstract_formal_spec" not in completed or len(rc_history) > cycle:
        return

    spec_files = _read_spec_files(deps)
    prior_note = ""
    if cycle > 0:
        prior_json = tools.read_out(deps, f"specs/reconciliation_cycle_{cycle}.json")
        if not prior_json.startswith("ERROR:"):
            prior_note = (
                f"\n\nPrior cycle {cycle} findings:\n{prior_json}\n"
                "Note whether previous critical discrepancies have been resolved, "
                "persisted, or worsened."
            )
    rc_result = await _run_stage(
        _reconcile,
        f"RECONCILE (cycle {cycle + 1}): compare abstract spec vs implementation spec. "
        "Classify every discrepancy and produce refinement obligations for critical ones."
        + prior_note
        + f"\n\n{spec_files}",
        deps,
        f"RECONCILE (cycle {cycle + 1})",
    )
    if rc_result and rc_result.output:
        rc: ReconciliationReport = rc_result.output
        rc_entry = {**rc.model_dump(), "cycle": cycle + 1}
        rc_history.append(rc_entry)
        tools.write_out(
            deps,
            f"specs/reconciliation_cycle_{cycle + 1}.json",
            rc.model_dump_json(indent=2),
        )
        git_ops.commit(
            deps.container_id,
            f"feat(spec): reconciliation cycle {cycle + 1} — abstract vs impl spec",
            glob="specs/",
        )
        critical = [d for d in rc.discrepancies if d.severity == "critical"]
        log.info(
            "Reconcile cycle %d: aligned=%d discrepancies=%d critical=%d "
            "refinement_obligations=%d",
            cycle + 1, len(rc.aligned), len(rc.discrepancies),
            len(critical), len(rc.refinement_obligations),
        )
        if critical:
            log.warning(
                "RECONCILE cycle %d found %d CRITICAL discrepancy/ies: %s",
                cycle + 1, len(critical),
                [d.kind + ": " + d.description[:60] for d in critical],
            )
    _checkpoint(deps)


async def _run_estimator(deps: AgentDeps, cycle: int) -> TheoremEstimate | None:
    """Run the effort estimator on the current formal spec + any reconciliation obligations."""
    formal = tools.read_out(deps, "specs/formal_spec.lean")
    if formal.startswith("ERROR:"):
        log.warning("Effort estimator: cannot read formal spec — %s", formal)
        return None
    rc_history = deps.progress.get("reconciliation_history", [])
    extras = ""
    if cycle < len(rc_history):
        obls = rc_history[cycle].get("refinement_obligations", [])
        if obls:
            extras = "\n\nRefinement obligations (from RECONCILE):\n" + "\n".join(
                f"  theorem {o['name']}: {o['statement'][:200]}" for o in obls
            )
    try:
        estimate = await subagents.estimate_theorem_effort(formal + extras)
        log.info(
            "Effort estimate: trivial=%d moderate=%d hard=%d misstated=%d",
            len(estimate.trivial), len(estimate.moderate),
            len(estimate.hard_acceptable), len(estimate.likely_misstated),
        )
        deps.progress["effort_estimate"] = estimate.model_dump()
        return estimate
    except Exception as e:
        log.warning("Effort estimator failed: %s — proceeding without estimate", e)
        return None


async def _run_prove_phase(deps: AgentDeps, cycle: int) -> tuple[bool, list[dict]]:
    """Run EFFORT-ESTIMATOR → PROVE (with inline PROOF-JUDGE on each successful build).

    PROOF-JUDGE is embedded inside check_and_judge: on every successful build PROVE receives
    a proof_judge verdict and stops if stagnant or done.  The final verdict is read from
    deps.progress['proof_verdict'] set by the last inline judge call.

    Returns (done, proof_amendments). done=True stops the cycle loop;
    proof_amendments carries mis-stated theorems for the next cycle.
    """
    if "proof_verdict" in deps.progress:
        log.info("proof_verdict already in progress — skipping proof stage")
        return True, []

    rc_history = deps.progress.get("reconciliation_history", [])
    all_critical = [
        {**d, "_cycle": rc_entry.get("cycle", "?")}
        for rc_entry in rc_history
        for d in rc_entry.get("discrepancies", [])
        if d.get("severity") == "critical"
    ]
    current_rc = rc_history[cycle] if cycle < len(rc_history) else {}
    rc_obligations = current_rc.get("refinement_obligations", [])
    prove_note = ""
    if rc_obligations:
        prove_note = (
            f"\n\nRECONCILIATION NOTE: {len(rc_obligations)} refinement obligation(s) "
            f"were generated in cycle {cycle + 1} to bridge the impl spec to the abstract spec. "
            f"Their sorry stubs are in specs/reconciliation_cycle_{cycle + 1}.json. "
            "You may add them to the spec file and attempt to prove them."
        )
    if all_critical:
        critical_lines = "\n".join(
            f"  [cycle {d['_cycle']} {d['kind']}] {d['description'][:100]}"
            for d in all_critical
        )
        prove_note += (
            f"\n\nCRITICAL ({len(all_critical)} across all cycles — do NOT paper over):\n"
            f"{critical_lines}\n"
            "These indicate a potential bug in the implementation or a mis-stated theorem. "
            "Leave the corresponding obligations unproved and note them clearly."
        )

    estimate = await _run_estimator(deps, cycle)
    if estimate:
        def _names(lst: list[str]) -> str:
            return ", ".join(lst) if lst else "none"
        prove_note += (
            f"\n\nEFFORT ESTIMATE:\n"
            f"  trivial (attempt):             {_names(estimate.trivial)}\n"
            f"  moderate (attempt):            {_names(estimate.moderate)}\n"
            f"  hard_acceptable (leave sorry): {_names(estimate.hard_acceptable)}\n"
            f"  likely_misstated (do not touch): {_names(estimate.likely_misstated)}\n"
            "Attempt only trivial and moderate theorems. Mark hard_acceptable as sorry "
            "without spending further effort."
        )

    try:
        await _run_stage(
            _prove,
            f"Proceed to PROVE (cycle {cycle + 1}). Attempt to fill in proofs "
            "for all sorry theorems in the spec. Do not alter any statement. "
            "Commit the result when done." + prove_note,
            deps,
            f"PROVE (cycle {cycle + 1})",
        )
    except UnexpectedModelBehavior as e:
        log.warning("PROVE stage failed after retries: %s", e)
    _checkpoint(deps)

    pv_data = deps.progress.get("proof_verdict")
    if pv_data is None:
        raise _PipelineAborted(
            f"PROVE cycle {cycle + 1} completed without a successful lake build — "
            "no proof verdict available"
        )

    pv = ProofVerdict(**pv_data)
    misstated = [t for t in pv.theorems if t.status == "likely_misstated"]
    proved    = [t for t in pv.theorems if t.status == "proved"]
    sorry_ok  = [t for t in pv.theorems if t.status == "sorry_acceptable"]
    log.info(
        "Proof-judge cycle %d: proved=%d sorry_acceptable=%d misstated=%s stagnant=%s",
        cycle + 1, len(proved), len(sorry_ok),
        [t.name for t in misstated] or "none", pv.stagnant,
    )

    if not misstated:
        log.info("No mis-stated theorems — proof stage complete")
        return True, []
    if pv.stagnant:
        log.warning("Proof judge reports stagnation — accepting current state")
        return True, []

    proof_amendments = [t.model_dump() for t in misstated]
    deps.progress.pop("verdict", None)
    deps.progress.pop("proof_verdict", None)
    log.info(
        "Feeding %d mis-stated theorem(s) back to formaliser for cycle %d",
        len(proof_amendments), cycle + 2,
    )
    return False, proof_amendments


async def _run_report(deps: AgentDeps, resume_note: str) -> str:
    """Run the REPORT stage and concatenate section files into VERIFICATION_REPORT.md."""
    verdict_data    = deps.progress.get("verdict", {})
    proof_verd_data = deps.progress.get("proof_verdict", {})
    proved_count    = sum(1 for t in proof_verd_data.get("theorems", []) if t.get("status") == "proved")
    sorry_count     = sum(1 for t in proof_verd_data.get("theorems", []) if t.get("status") == "sorry_acceptable")
    misstated_count = sum(1 for t in proof_verd_data.get("theorems", []) if t.get("status") == "likely_misstated")
    rc_history      = deps.progress.get("reconciliation_history", [])
    rc_critical_ever = sum(
        1 for rc_entry in rc_history
        for d in rc_entry.get("discrepancies", [])
        if d.get("severity") == "critical"
    )
    rc_cycles        = len(rc_history)
    rc_obligations_n = sum(len(rc_entry.get("refinement_obligations", [])) for rc_entry in rc_history)

    report_files = _read_spec_files(deps)
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    if lean_path:
        lean_content = tools.read_out(deps, lean_path)
        if not lean_content.startswith("ERROR:"):
            report_files = f"### {lean_path}\n{lean_content}\n\n{report_files}"
    for i, rc_entry in enumerate(rc_history, 1):
        rc_json = tools.read_out(deps, f"specs/reconciliation_cycle_{i}.json")
        if not rc_json.startswith("ERROR:"):
            report_files += f"\n\n### specs/reconciliation_cycle_{i}.json\n{rc_json}"

    report_result = await _run_stage(
        _report,
        f"Proceed to REPORT. "
        f"Spec-judge: approved={verdict_data.get('approved')}, score={verdict_data.get('score')}. "
        f"Proof-judge: proved={proved_count}, sorry_acceptable={sorry_count}, "
        f"likely_misstated={misstated_count}. "
        f"Reconciliation: {rc_cycles} cycle(s), critical_discrepancies_ever={rc_critical_ever}, "
        f"total_refinement_obligations={rc_obligations_n}."
        f"\n\n{report_files}" + resume_note,
        deps, "REPORT",
    )

    _REPORT_SECTIONS = [
        "report/01_overview.md", "report/02_translation.md",
        "report/03_abstract_spec.md", "report/04_implementation_spec.md",
        "report/05_spec_judge.md", "report/06_reconciliation.md",
        "report/07_proofs.md", "report/08_summary.md",
    ]
    parts = [
        content for sf in _REPORT_SECTIONS
        for content in [tools.read_out(deps, sf)]
        if not content.startswith("ERROR:")
    ]
    if parts:
        report_text = "\n\n".join(parts)
        log.info("Concatenating %d/%d report sections into VERIFICATION_REPORT.md",
                 len(parts), len(_REPORT_SECTIONS))
    else:
        report_text = report_result.output if report_result else ""
        if report_text:
            log.warning("No report/ sections found — falling back to agent text output")
        else:
            log.warning("REPORT stage produced no sections and no text output")

    if report_text:
        try:
            tools.write_out(deps, "VERIFICATION_REPORT.md", report_text)
            git_ops.commit(deps.container_id, "stage/report: final pipeline report",
                           glob="VERIFICATION_REPORT.md")
            log.info("Report written and committed (%d chars)", len(report_text))
        except Exception as e:
            log.warning("Could not write report: %s", e)

    _checkpoint(deps)
    return report_text or "(no report generated)"


# ── main entry point ──────────────────────────────────────────────────────────

async def run_session(deps: AgentDeps) -> str:
    """Drive the pipeline stage by stage and return a final summary string."""
    resuming = bool(deps.progress)

    if resuming:
        log.info("Resuming session — completed stages: %s", sorted(deps.progress.keys()))

    resume_note = (
        "\n\nSESSION RESUMED — the Docker container is fresh but all previously "
        "generated artefacts have been restored. Continue from where you left off."
        if resuming else ""
    )

    try:
        resume_note = await _run_doc_stages(deps, resume_note)
        resume_note = await _run_translate_stages(deps, resume_note)

        proof_amendments: list[dict] = []
        for cycle in range(_CYCLE_CAP):
            resume_note = await _run_spec_phase(deps, cycle, resume_note, proof_amendments)
            await _run_reconcile_phase(deps, cycle)
            done, proof_amendments = await _run_prove_phase(deps, cycle)
            if done:
                break
        else:
            log.warning("Spec+proof cycle loop hit cap of %d cycles", _CYCLE_CAP)

        result = await _run_report(deps, resume_note)
        u = telemetry.session.as_dict()
        log.info(
            "Pipeline complete — session usage: in=%d out=%d cache_read=%d cache_write=%d total=%d/%s",
            u["input_tokens"], u["output_tokens"],
            u["cache_read_tokens"], u["cache_write_tokens"],
            u["total_tokens"], telemetry.budget or "∞",
        )
        return result

    except _PipelineAborted as e:
        log.error("Pipeline aborted: %s", e)
        return f"Pipeline aborted: {e}"
