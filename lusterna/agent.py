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

from . import checkpoint, docs, factory, git_ops, subagents, tools
from .subagents import (
    AbstractInformalSpec, AbstractFormalSpec,
    InformalSpec, FormalSpec, JudgeVerdict, ProofVerdict, ReconciliationReport,
)
from .state import AgentDeps
from . import config

log = logging.getLogger(__name__)


# ── shared tool functions ─────────────────────────────────────────────────────
# Plain functions — registered on whichever stage agents need them via
# agent.tool(fn).  Actual logic stays in tools.py / git_ops.py.

def list_files(ctx: RunContext[AgentDeps], extension: str = "rs") -> list[str]:
    """List files in the repo with the given extension (e.g. 'rs', 'toml', 'lean')."""
    return tools.list_files(ctx.deps, extension)


def read_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a source file from the Rust repository (repo-relative path)."""
    return tools.read_file(ctx.deps, path)


def read_output_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a generated file from /workspace/out.

    *path* may be relative or an absolute path inside /workspace/out.
    """
    _OUT_PREFIX = "/workspace/out/"
    if path.startswith(_OUT_PREFIX):
        path = path[len(_OUT_PREFIX):]
    return tools.read_output_file(ctx.deps, path)


def write_file(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    """Write content to a file in /workspace/out.

    *path* may be relative (e.g. 'VERIFICATION_REPORT.md') or an absolute path
    inside /workspace/out (e.g. '/workspace/out/VERIFICATION_REPORT.md') — both
    are accepted. Returns an ERROR: string on failure so the model can recover.
    """
    # Normalise: strip the known container output prefix so the model can use
    # absolute paths without triggering the relative-path guard in tools.write_file.
    _OUT_PREFIX = "/workspace/out/"
    if path.startswith(_OUT_PREFIX):
        path = path[len(_OUT_PREFIX):]
    elif path == "/workspace/out":
        path = "."
    try:
        return tools.write_file(ctx.deps, path, content)
    except (ValueError, IOError) as e:
        return f"ERROR: {e}"


def append_file(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    """Append content to a file in /workspace/out (creates the file if absent).

    Use this to write large files in sections without hitting output token limits.
    Each call appends *content* immediately after whatever was previously written.
    Returns an ERROR: string on failure so the model can recover.
    """
    _OUT_PREFIX = "/workspace/out/"
    if path.startswith(_OUT_PREFIX):
        path = path[len(_OUT_PREFIX):]
    try:
        return tools.append_file(ctx.deps, path, content)
    except (ValueError, IOError) as e:
        return f"ERROR: {e}"


def write_rust_file(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    """Overwrite a Rust source file in the repo.

    Use to remove untranslatable constructs (vec!, println!, main body, etc.)
    before retrying run_aeneas.  Path must be repo-relative.
    Returns an ERROR: string if the path is invalid.
    """
    try:
        return tools.write_rust_file(ctx.deps, path, content)
    except (ValueError, IOError) as e:
        return f"ERROR: {e}"


def run_aeneas(ctx: RunContext[AgentDeps], entry_file: str) -> dict:
    """Translate entry_file to Lean 4 via Charon + Aeneas.

    Stores result in progress['aeneas'] on success and saves a checkpoint.
    Inspect aeneas_errors / charon_errors and use write_rust_file to fix issues,
    then call run_aeneas again (max 2 retries).
    """
    result = tools.run_aeneas(ctx.deps, entry_file)
    if result.get("success") or result.get("lean_files"):
        ctx.deps.progress["aeneas"] = result
        _checkpoint(ctx.deps)
    return result


def search_mathlib(ctx: RunContext[AgentDeps], query: str, max_results: int = 8) -> list[dict]:
    """Search Mathlib4 for lemmas by name fragment or type signature (via Loogle).

    Use this when you need a specific Mathlib lemma and are not sure of its exact
    name.  Examples:
      search_mathlib("Nat.fib_mono")          — find the monotonicity lemma for fib
      search_mathlib("Monotone Nat.fib")      — search by type shape
      search_mathlib("UInt64 mod")            — find UInt64 modular arithmetic lemmas
    Returns up to max_results hits with name, type, module, and doc fields.
    Runs outside the container — no network restriction applies.
    """
    return tools.search_mathlib(query, max_results=max_results)


def git_commit(ctx: RunContext[AgentDeps], message: str) -> str:
    """Stage all pending changes in /workspace/out and create a git commit."""
    return tools.git_commit(ctx.deps, message)


def git_log(ctx: RunContext[AgentDeps], n: int = 10) -> str:
    """Show the last n commits in /workspace/out."""
    return tools.git_log(ctx.deps, n=n)


async def infer_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Call the spec-inferrer subagent to derive an informal specification.

    Reads the Aeneas-translated Lean file and the design document, then returns
    a structured InformalSpec stored in progress['informal_spec'].
    """
    lean_path = ctx.deps.progress.get("aeneas", {}).get("lean_path", "")
    lean_code = tools.read_output_file(ctx.deps, lean_path) if lean_path else ""
    try:
        spec = await subagents.infer_informal_spec(lean_code, ctx.deps.design_doc)
    except UnexpectedModelBehavior as e:
        return {"error": f"infer_spec subagent failed: {e}"}
    ctx.deps.progress["informal_spec"] = spec.model_dump()
    tools.write_file(ctx.deps, "specs/informal_spec.json", spec.model_dump_json(indent=2))
    git_ops.commit(ctx.deps.container_id, "feat(spec): informal specification", glob="specs/")
    _checkpoint(ctx.deps)
    return spec.model_dump()


async def formalise_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Call the formal-spec subagent to derive Lean 4 theorem stubs.

    Requires progress['informal_spec'] to exist (call infer_spec first).
    Stores result in progress['formal_spec'] and commits specs/formal_spec.lean.
    If the subagent fails, return an error dict and write the spec manually with
    write_file based on the informal spec.
    """
    inf_data = ctx.deps.progress.get("informal_spec")
    if not inf_data:
        return {"error": "prerequisite missing — call infer_spec first"}
    informal = InformalSpec(**inf_data)
    lean_path = ctx.deps.progress.get("aeneas", {}).get("lean_path", "")
    lean_code = tools.read_output_file(ctx.deps, lean_path) if lean_path else ""
    try:
        formal = await subagents.derive_formal_spec(informal, lean_code)
    except UnexpectedModelBehavior as e:
        return {
            "error": (
                f"formalise_spec subagent failed: {e} — "
                "retry once or write the spec manually with write_file"
            )
        }
    ctx.deps.progress["formal_spec"] = formal.model_dump()
    tools.write_file(
        ctx.deps, "specs/formal_spec.lean",
        formal.lean_definitions + "\n\n" + formal.lean_theorem_stubs,
    )
    git_ops.commit(ctx.deps.container_id, "feat(spec): formal specification stubs", glob="specs/")
    _checkpoint(ctx.deps)
    return formal.model_dump()


async def infer_abstract_informal_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Call the doc-inferrer to derive an abstract informal spec from the design document.

    Reads the design document ONLY — no Rust source, no Lean translation.
    Stores result in progress['abstract_informal_spec'] and commits specs/.
    """
    try:
        spec = await subagents.infer_abstract_informal_spec(ctx.deps.design_doc)
    except UnexpectedModelBehavior as e:
        return {"error": f"doc-inferrer failed: {e}"}
    ctx.deps.progress["abstract_informal_spec"] = spec.model_dump()
    tools.write_file(ctx.deps, "specs/abstract_informal_spec.json", spec.model_dump_json(indent=2))
    git_ops.commit(ctx.deps.container_id, "feat(spec): abstract informal specification", glob="specs/")
    _checkpoint(ctx.deps)
    return spec.model_dump()


async def formalise_abstract_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Run the doc-formaliser convergence loop to produce Lean 4 abstract theorem stubs.

    Requires progress['abstract_informal_spec']. Iterates up to 3 rounds: the
    doc-formaliser flags ambiguities; the doc-inferrer resolves them from the design
    document; repeat until converged or the round cap is reached.
    Stores result in progress['abstract_formal_spec'] and commits specs/.
    """
    inf_data = ctx.deps.progress.get("abstract_informal_spec")
    if not inf_data:
        return {"error": "prerequisite missing — call infer_abstract_informal_spec first"}
    abstract_informal = AbstractInformalSpec(**inf_data)

    _MAX_ROUNDS = 3
    abstract_formal: AbstractFormalSpec | None = None
    for round_num in range(_MAX_ROUNDS):
        try:
            abstract_formal = await subagents.derive_abstract_formal_spec(abstract_informal)
        except UnexpectedModelBehavior as e:
            return {"error": f"doc-formaliser failed: {e}"}

        if not abstract_formal.ambiguities or round_num == _MAX_ROUNDS - 1:
            break

        log.info(
            "Doc-formaliser flagged %d ambiguity/ies — refining (round %d/%d)",
            len(abstract_formal.ambiguities), round_num + 1, _MAX_ROUNDS,
        )
        try:
            abstract_informal = await subagents.refine_abstract_informal_spec(
                ctx.deps.design_doc, abstract_informal, abstract_formal.ambiguities
            )
        except UnexpectedModelBehavior as e:
            log.warning("Doc-inferrer refinement failed: %s — using last spec", e)
            break

    if abstract_formal is None:
        return {"error": "doc-formaliser produced no output"}

    ctx.deps.progress["abstract_informal_spec"] = abstract_informal.model_dump()
    ctx.deps.progress["abstract_formal_spec"] = abstract_formal.model_dump()
    tools.write_file(ctx.deps, "specs/abstract_informal_spec.json", abstract_informal.model_dump_json(indent=2))
    tools.write_file(
        ctx.deps, "specs/abstract_formal_spec.lean",
        abstract_formal.lean_definitions + "\n\n" + abstract_formal.lean_theorem_stubs,
    )
    git_ops.commit(ctx.deps.container_id, "feat(spec): abstract formal specification", glob="specs/")
    _checkpoint(ctx.deps)
    return abstract_formal.model_dump()


def check_lean(ctx: RunContext[AgentDeps], lean_file: str) -> dict:
    """Run `lake build` in the Lean project and return {success, stdout, stderr}.

    The result is stored in progress['lean_build'] and a checkpoint is saved.
    Always call this after writing or modifying any Lean file.
    """
    result = tools.check_lean(ctx.deps, lean_file)
    ctx.deps.progress["lean_build"] = result
    _checkpoint(ctx.deps)
    return result


def get_build_result(ctx: RunContext[AgentDeps]) -> dict:
    """Return the most recent lake build result from progress."""
    return ctx.deps.progress.get(
        "lean_build",
        {"success": False, "stdout": "", "stderr": "check_lean has not been called yet"},
    )


# ── pipeline stages ───────────────────────────────────────────────────────────

_doc_infer = factory.make_stage_agent("""
You are the DOC-INFER stage of the Lusterna pipeline.

Derive an abstract informal specification from the design document ALONE.
You have NO access to the Rust source code or the Lean translation — only the design document.

Call infer_abstract_informal_spec. It reads the design document and returns a structured
AbstractInformalSpec: preconditions, postconditions, invariants, edge cases, and open
questions where the design doc is silent.

When the tool returns (success or error), report the result and stop.
Do not call any other tool.
""",
)
_doc_infer.tool(infer_abstract_informal_spec)


_doc_formalise = factory.make_stage_agent("""
You are the DOC-FORMALISE stage of the Lusterna pipeline.

Produce a Lean 4 abstract formal specification from the abstract informal spec.
You have NO access to the Rust source code or the Lean translation.

Call formalise_abstract_spec. It runs an internal convergence loop:
  1. The doc-formaliser turns the AbstractInformalSpec into Lean 4 theorem stubs.
  2. If ambiguities are flagged, the doc-inferrer resolves them from the design document.
  3. Repeat up to 3 rounds until converged.

The result is committed to specs/abstract_formal_spec.lean.
When the tool returns (success or error), report the result and stop.
Do not call any other tool.
""",
)
_doc_formalise.tool(formalise_abstract_spec)


_explore = factory.make_stage_agent("""
You are the EXPLORE stage of the Lusterna formal verification pipeline.

Understand the Rust codebase before translation begins:
- List all .rs and .toml files.
- Read the key source files (lib.rs, main.rs, Cargo.toml).
- Identify functions to be translated and flag obvious Aeneas incompatibilities
  (vec!, println!, trait objects, unsupported std types, etc.).

Write a short structured summary of findings. Then stop — do not run Aeneas.
""",
)
_explore.tool(list_files)
_explore.tool(read_file)


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
""" + docs.FOR_TRANSLATE,
)
_translate.tool(list_files)
_translate.tool(read_file)
_translate.tool(read_output_file)
_translate.tool(write_rust_file)
_translate.tool(run_aeneas)
_translate.tool(git_commit)
_translate.tool(git_log)


_infer = factory.make_stage_agent("""
You are the INFER stage of the Lusterna pipeline.

Derive an informal specification from the Aeneas-translated Lean output:

1. Read the translated Lean file(s) to understand function signatures and structure.
2. Call infer_spec — it spawns a subagent that reads the Lean code and the design
   document and returns a structured InformalSpec (preconditions, postconditions,
   invariants, edge cases).
3. If infer_spec returns an error dict, try once more.

When infer_spec succeeds, your job is done. Do not proceed to formalise_spec.
""",
)
_infer.tool(list_files)
_infer.tool(read_output_file)
_infer.tool(infer_spec)


_formalise = factory.make_stage_agent("""
You are the FORMALISE+BUILD stage of the Lusterna pipeline.

Produce a Lean 4 formal specification that compiles with `lake build`.
Write theorem stubs only — use `sorry` for all proofs. Do NOT attempt proofs.

1. Call formalise_spec — it spawns a subagent that turns the informal spec into Lean 4
   theorem stubs and commits specs/formal_spec.lean.
   If it returns an error, retry once; if it fails again, write the spec manually
   with write_file using the informal spec in progress.

2. Write the spec file to lean/<CrateName>Spec.lean (if formalise_spec did not).
   Make sure lean/lakefile.lean declares it as a lean_lib target.

3. Call check_lean to run `lake build`.

4. If the build fails:
   - Read stdout/stderr carefully.
   - Fix type errors, missing imports, namespace issues with write_file.
   - Call check_lean again. Repeat up to 3 total build attempts.

5. Commit everything once the build passes (or after all attempts, noting any failures).

Do NOT attempt proofs — that is the PROVE stage's responsibility.
""" + docs.FOR_FORMALISE,
)
_formalise.tool(list_files)
_formalise.tool(read_file)
_formalise.tool(read_output_file)
_formalise.tool(write_file, retries=2)
_formalise.tool(formalise_spec)
_formalise.tool(check_lean)
_formalise.tool(git_commit)
_formalise.tool(git_log)


_judge = factory.make_stage_agent("""
You are the JUDGE stage of the Lusterna pipeline.

Evaluate the formal Lean 4 specification and return a structured JudgeVerdict that
includes both an overall verdict and a per-component breakdown.

Steps:
1. Call get_build_result to see whether lake build passed.
2. Read the formal spec file(s) (lean/*Spec.lean) and the Aeneas translation (lean/*.lean).
3. Read specs/informal_spec.json for the implementation-derived informal spec.
4. Read specs/abstract_formal_spec.lean — this is the ABSTRACT specification derived
   from the design document alone, with no knowledge of the Rust implementation.
   It represents the intended behaviour as the author described it, independent of
   how the code was written. Use it as a ground-truth reference: if a theorem in
   the implementation spec contradicts or is weaker than the abstract spec, flag it
   as a critical issue. Gaps in the abstract spec (things it does not mention) are
   acceptable — they represent design-doc silence, not discrepancies.

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
    1. This is not the first judging round (there is a prior verdict in the message history).
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
_judge.tool(get_build_result)
_judge.tool(list_files)
_judge.tool(read_output_file)


_reconcile = factory.make_stage_agent("""
You are the RECONCILE stage of the Lusterna pipeline.

Compare the abstract formal specification (derived from the design document alone)
against the implementation formal specification (derived from the Aeneas translation).
Return a structured ReconciliationReport.

Steps:
1. Read specs/abstract_formal_spec.lean — this is the ABSTRACT spec. It was produced
   with zero knowledge of the Rust source. It represents design intent.
2. Read lean/*Spec.lean — this is the IMPLEMENTATION spec, derived from the Aeneas
   translation of the Rust code.
3. For each theorem/definition, determine whether the two specs agree, diverge, or
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
_reconcile.tool(list_files)
_reconcile.tool(read_output_file)


_prove = factory.make_stage_agent("""
You are the PROVE stage of the Lusterna pipeline.

The formal spec has been approved by the spec judge. Your job is to attempt to
prove as many theorems and lemmas as possible using Lean 4 tactics, without
changing any theorem or definition statements.

Workflow:
1. List and read the spec file(s) (lean/*Spec.lean).
2. For each theorem/lemma with a `sorry` proof, attempt tactics in this order:
     rfl, simp, omega, norm_num, decide, native_decide, ring, linarith,
     then induction / cases with the above tactics on sub-goals.
3. After editing, call check_lean. If the build fails, revert failing proofs
   to `sorry` (do not touch the statement) and call check_lean again.
4. Commit the result with git_commit.

STRICT RULES:
- NEVER alter a theorem's statement (the part before `:= by`).
- NEVER introduce an axiom or `#check` that weakens the spec.
- If a proof takes more than 2-3 tactic attempts, leave it as `sorry` and move on.
- It is acceptable — even expected — to leave hard theorems as `sorry`.
""" + docs.FOR_PROVE,
)
_prove.tool(list_files)
_prove.tool(read_output_file)
_prove.tool(write_file, retries=2)
_prove.tool(check_lean)
_prove.tool(search_mathlib)
_prove.tool(git_commit)
_prove.tool(git_log)


_proof_judge = factory.make_stage_agent("""
You are the PROOF JUDGE stage of the Lusterna pipeline.

Evaluate every theorem and lemma in the formal spec and return a ProofVerdict.

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
  1. This is not the first proof-judge round (prior verdict exists in message history).
  2. The set of likely_misstated theorems is identical to the previous round.
  3. The proof automator made no changes to those theorems' statements or proof attempts.
  Default to stagnant=false.
""",
    output_type=ProofVerdict,
    retries=3,
)
_proof_judge.tool(get_build_result)
_proof_judge.tool(list_files)
_proof_judge.tool(read_output_file)


_report = factory.make_stage_agent("""
You are the REPORT stage of the Lusterna formal verification pipeline.

Write each section of the verification report as a SEPARATE FILE under report/,
using write_file once per section. The calling code will concatenate them in order
into VERIFICATION_REPORT.md — do NOT write that file yourself and do NOT call
git_commit.

Write exactly these files, in this order:

  report/01_overview.md
      Title, one-paragraph executive summary, overview table (translation result,
      spec-judge score, proved/sorry/misstated counts, critical discrepancies).

  report/02_translation.md
      What was translated: entry file, Rust changes required (stubs, removals),
      Aeneas output files, any partial-translation caveats.

  report/03_abstract_spec.md
      The abstract specification derived from the design document alone
      (specs/abstract_formal_spec.lean). List the key theorems and definitions with
      a one-line gloss for each. Note any open_questions the doc-inferrer flagged.

  report/04_implementation_spec.md
      The implementation informal spec (specs/informal_spec.json) and formal spec
      (lean/*Spec.lean). List every theorem stub with its statement and a one-line
      explanation. Include the lake build result.

  report/05_spec_judge.md
      Spec-judge verdict: overall score, approved/not, per-component breakdown.
      Quote the judge's issues and suggestions for any component scoring < 8.

  report/06_reconciliation.md
      Reconciliation results for every cycle (specs/reconciliation_cycle_N.json).
      For each cycle: aligned components, discrepancies (with kind, severity,
      description), refinement obligations. Flag any discrepancy that appeared in
      an earlier cycle but vanished later — that is suspicious.

  report/07_proofs.md
      Proof-judge verdict: per-theorem classification (proved/sorry_acceptable/
      likely_misstated), proof sketch for each proved theorem, suggested strategy
      for each sorry_acceptable, precise reason for any likely_misstated.

  report/08_summary.md
      Open proof obligations (each sorry with a concrete next step), known gaps
      and limitations, overall verdict paragraph.

Be thorough — do not summarise away detail that would help a reader understand
what was verified, what was found, and what remains open.
""",
)
_report.tool(list_files)
_report.tool(read_file)
_report.tool(read_output_file)
_report.tool(write_file, retries=2)
_report.tool(git_log)


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


async def _run_stage(agent: Agent, prompt: str, deps: AgentDeps, label: str) -> Any:
    """Run one stage agent to completion. Each stage starts with no prior history.

    Stages communicate via the filesystem and deps.progress, not via conversation
    context — so no history is passed in or accumulated across stages.
    Re-raises UnexpectedModelBehavior; judge stages catch it locally.
    """
    log.info("─── Stage: %s ───", label)
    deps.message_history = []
    async with agent.iter(prompt, deps=deps) as run:
        async for _node in run:
            pass
        result = run.result
    factory.record_usage(result.usage)
    u = factory.get_usage()
    log.info(
        "Stage %s complete — stage usage: in=%d out=%d cache_read=%d | "
        "session total=%d/%s",
        label,
        getattr(result.usage, "input_tokens", 0) or 0,
        getattr(result.usage, "output_tokens", 0) or 0,
        getattr(result.usage, "cache_read_tokens", 0) or 0,
        u["total_tokens"], u["budget"] or "∞",
    )
    return result


# ── pipeline sub-functions ────────────────────────────────────────────────────

async def _run_doc_stages(deps: AgentDeps, resume_note: str) -> str:
    """Run DOC-INFER and DOC-FORMALISE (skipped if already in progress)."""
    completed = set(deps.progress.keys())

    if "abstract_informal_spec" not in completed:
        await _run_stage(
            _doc_infer,
            "Begin DOC-INFER. Call infer_abstract_informal_spec to derive the abstract "
            "informal specification from the design document." + resume_note,
            deps, "DOC-INFER",
        )
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "abstract_informal_spec" not in completed:
            log.warning("DOC-INFER produced no output — proceeding without abstract spec")

    if "abstract_informal_spec" in completed and "abstract_formal_spec" not in completed:
        await _run_stage(
            _doc_formalise,
            "Proceed to DOC-FORMALISE. Call formalise_abstract_spec to produce "
            "Lean 4 abstract theorem stubs from the abstract informal spec." + resume_note,
            deps, "DOC-FORMALISE",
        )
        _checkpoint(deps)
        resume_note = ""

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
        await _run_stage(
            _infer,
            "Proceed to INFER. Call infer_spec to derive the informal specification "
            "from the Lean output and the design document." + resume_note,
            deps, "INFER",
        )
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "informal_spec" not in completed:
            raise _PipelineAborted("Informal spec inference failed.")

    return resume_note


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
                    "Proceed to FORMALISE+BUILD. Call formalise_spec to derive "
                    "Lean 4 theorem stubs (all sorry), write the spec file to lean/, "
                    "register it in the lakefile, then call check_lean until the "
                    "build passes (max 3 build attempts)." + resume_note
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
                        "Revise the spec based on the feedback above, "
                        "then call check_lean to confirm the build passes."
                    )

            await _run_stage(
                _formalise, formalise_prompt, deps,
                f"FORMALISE (cycle {cycle + 1}, round {spec_attempt + 1})",
            )
            _checkpoint(deps)
            resume_note = ""

        build_ok = deps.progress.get("lean_build", {}).get("success", False)
        try:
            sj_result = await _run_stage(
                _judge,
                f"Proceed to SPEC-JUDGE. Evaluate the theorem statements only "
                f"(ignore sorry proofs). "
                f"lake build {'passed ✓' if build_ok else 'FAILED ✗ — approved must be false'}.",
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

    rc_result = await _run_stage(
        _reconcile,
        f"Proceed to RECONCILE (cycle {cycle + 1}). "
        "Compare specs/abstract_formal_spec.lean (design intent, no implementation "
        "knowledge) against lean/*Spec.lean (implementation spec). "
        "Classify every discrepancy and produce refinement obligations for critical ones.",
        deps,
        f"RECONCILE (cycle {cycle + 1})",
    )
    if rc_result and rc_result.output:
        rc: ReconciliationReport = rc_result.output
        rc_entry = {**rc.model_dump(), "cycle": cycle + 1}
        rc_history.append(rc_entry)
        tools.write_file(
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


async def _run_prove_phase(deps: AgentDeps, cycle: int) -> tuple[bool, list[dict]]:
    """Run PROVE+PROOF-JUDGE for one cycle.

    Returns (done, proof_amendments). done=True stops the cycle loop;
    proof_amendments carries mis-stated theorems for the next cycle.
    """
    completed = set(deps.progress.keys())

    if "proof_verdict" in completed:
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
        log.warning("PROVE stage failed after retries: %s — continuing to PROOF-JUDGE with partial proofs", e)
    _checkpoint(deps)

    build_ok = deps.progress.get("lean_build", {}).get("success", False)
    try:
        pj_result = await _run_stage(
            _proof_judge,
            f"Proceed to PROOF-JUDGE (cycle {cycle + 1}). "
            f"lake build {'passed ✓' if build_ok else 'FAILED ✗'}. "
            "Classify every theorem as proved / sorry_acceptable / likely_misstated.",
            deps,
            f"PROOF-JUDGE (cycle {cycle + 1})",
        )
    except UnexpectedModelBehavior as e:
        log.warning("Proof judge failed after retries: %s — stopping", e)
        return True, []

    if not (pj_result and pj_result.output):
        log.warning("Proof judge produced no output — stopping")
        return True, []

    pv: ProofVerdict = pj_result.output
    deps.progress["proof_verdict"] = pv.model_dump()
    misstated = [t for t in pv.theorems if t.status == "likely_misstated"]
    proved    = [t for t in pv.theorems if t.status == "proved"]
    sorry_ok  = [t for t in pv.theorems if t.status == "sorry_acceptable"]
    log.info(
        "Proof-judge cycle %d: proved=%d sorry_acceptable=%d misstated=%s stagnant=%s",
        cycle + 1, len(proved), len(sorry_ok),
        [t.name for t in misstated] or "none", pv.stagnant,
    )
    _checkpoint(deps)

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

    report_result = await _run_stage(
        _report,
        f"Proceed to REPORT. "
        f"Spec-judge: approved={verdict_data.get('approved')}, score={verdict_data.get('score')}. "
        f"Proof-judge: proved={proved_count}, sorry_acceptable={sorry_count}, "
        f"likely_misstated={misstated_count}. "
        f"Reconciliation: {rc_cycles} cycle(s), critical_discrepancies_ever={rc_critical_ever}, "
        f"total_refinement_obligations={rc_obligations_n}. "
        f"Reconciliation reports are at specs/reconciliation_cycle_N.json (N=1..{rc_cycles}). "
        "Read the artefacts (lean/*.lean, specs/*.json) "
        "and produce the full VERIFICATION_REPORT.md content as your response." + resume_note,
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
        for content in [tools.read_output_file(deps, sf)]
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
            tools.write_file(deps, "VERIFICATION_REPORT.md", report_text)
            git_ops.commit(deps.container_id, "stage/report: final pipeline report",
                           glob="VERIFICATION_REPORT.md")
            log.info("Report written and committed (%d chars)", len(report_text))
        except Exception as e:
            log.warning("Could not write report: %s", e)

    _checkpoint(deps)
    usage = factory.get_usage()
    log.info(
        "Pipeline complete — session usage: in=%d out=%d "
        "cache_read=%d cache_write=%d total=%d/%s requests=%d",
        usage["input_tokens"], usage["output_tokens"],
        usage["cache_read_tokens"], usage["cache_write_tokens"],
        usage["total_tokens"], usage["budget"] or "∞", usage["requests"],
    )
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

        return await _run_report(deps, resume_note)

    except _PipelineAborted as e:
        log.error("Pipeline aborted: %s", e)
        return f"Pipeline aborted: {e}"
