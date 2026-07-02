"""Stage-based pipeline: one Agent per stage, Python drives sequencing and loops."""
import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import ModelRequestContext

from . import checkpoint, compaction, docs, git_ops, subagents, tools
from .subagents import InformalSpec, FormalSpec, JudgeVerdict, ProofVerdict
from .state import AgentDeps
from . import config

log = logging.getLogger(__name__)


# ── message-history snapshot hook ─────────────────────────────────────────────
# Shared across all stage agents. Before every model request the hook writes the
# current accumulated messages into deps.message_history so that mid-stage
# _checkpoint() calls always capture up-to-date history.

_hooks = Hooks()


@_hooks.on.before_model_request
async def _snapshot_messages(
    ctx: RunContext[AgentDeps], model_ctx: ModelRequestContext
) -> ModelRequestContext | None:
    ctx.deps.message_history = list(model_ctx.messages)
    return model_ctx


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


# ── stage agents ──────────────────────────────────────────────────────────────

_explore = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    capabilities=[_hooks],
    instructions="""
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


_translate = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    capabilities=[_hooks],
    instructions="""
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


_infer = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    capabilities=[_hooks],
    instructions="""
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


_formalise = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    capabilities=[_hooks],
    instructions="""
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
_formalise.tool(write_file)
_formalise.tool(formalise_spec)
_formalise.tool(check_lean)
_formalise.tool(git_commit)
_formalise.tool(git_log)


_judge = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    output_type=JudgeVerdict,
    retries=3,
    capabilities=[_hooks],
    instructions="""
You are the JUDGE stage of the Lusterna pipeline.

Evaluate the formal Lean 4 specification and return a structured JudgeVerdict that
includes both an overall verdict and a per-component breakdown.

Steps:
1. Call get_build_result to see whether lake build passed.
2. Read the formal spec file(s) (lean/*Spec.lean) and the Aeneas translation (lean/*.lean).
3. Read specs/informal_spec.json for the reference specification.

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
)
_judge.tool(get_build_result)
_judge.tool(list_files)
_judge.tool(read_output_file)


_prove = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    capabilities=[_hooks],
    instructions="""
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
_prove.tool(write_file)
_prove.tool(check_lean)
_prove.tool(search_mathlib)
_prove.tool(git_commit)
_prove.tool(git_log)


_proof_judge = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    output_type=ProofVerdict,
    retries=3,
    capabilities=[_hooks],
    instructions="""
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
)
_proof_judge.tool(get_build_result)
_proof_judge.tool(list_files)
_proof_judge.tool(read_output_file)


_report = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    capabilities=[_hooks],
    instructions="""
You are the REPORT stage of the Lusterna formal verification pipeline.

Read the generated artefacts and produce the full text of VERIFICATION_REPORT.md
as your final response (plain Markdown, no surrounding commentary).

The report must cover:
- What was translated and how (Charon/Aeneas, any Rust changes required)
- The informal specification (key preconditions, postconditions, invariants, edge cases)
- The formal specification: theorems stated, which are proved vs sorry
- The lake build result and judge verdict (score, issues, suggestions)
- Open proof obligations: each sorry with a brief suggested proof strategy
- Known gaps or limitations

After reading the files, output ONLY the Markdown report — nothing else.
The calling code will write it to VERIFICATION_REPORT.md and commit it.
""",
)
_report.tool(list_files)
_report.tool(read_file)
_report.tool(read_output_file)
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
        messages=deps.message_history,
    )


async def _run_stage(
    agent: Agent,
    prompt: str,
    deps: AgentDeps,
    history: list,
    label: str,
) -> tuple[Any, list]:
    """Run one stage agent to completion, handling compaction. Returns (result, history).

    Catches UnexpectedModelBehavior so a single bad tool call doesn't crash the
    entire pipeline — the caller can inspect deps.progress to decide whether to
    retry or abort.
    """
    log.info("─── Stage: %s ───", label)
    result = None
    try:
        async with agent.iter(prompt, deps=deps, message_history=history) as run:
            async for _node in run:
                if compaction.needs_compaction(deps.message_history):
                    log.info("Compacting context in stage %s", label)
                    deps.message_history = await compaction.compact(
                        deps.message_history,
                        summarise_fn=subagents.summarise_history,
                    )
            result = run.result
    except UnexpectedModelBehavior as e:
        log.warning("Stage %s hit UnexpectedModelBehavior: %s", label, e)
    if result:
        history = result.all_messages()
        deps.message_history = history
    return result, history


# ── main entry point ──────────────────────────────────────────────────────────

async def run_session(deps: AgentDeps) -> str:
    """Drive the pipeline stage by stage and return a final summary string."""
    history: list = list(deps.message_history)
    completed = set(deps.progress.keys())
    resuming = bool(history)

    if resuming:
        log.info("Resuming session — completed stages: %s", sorted(completed))

    # A short note appended to the first prompt when resuming so the agent
    # knows the container is fresh even though it has prior history.
    resume_note = (
        "\n\nSESSION RESUMED — the Docker container is fresh but all previously "
        "generated artefacts have been restored. Continue from where you left off."
        if resuming else ""
    )

    # ── EXPLORE ───────────────────────────────────────────────────────────────
    # Skip if TRANSLATE already completed (aeneas in progress).
    if "aeneas" not in completed:
        _, history = await _run_stage(
            _explore,
            f"Begin EXPLORE for the Rust repository at {deps.repo_path}.\n"
            f"Design document:\n{deps.design_doc[:2000]}" + resume_note,
            deps, history, "EXPLORE",
        )
        _checkpoint(deps)
        resume_note = ""  # consumed

    # ── TRANSLATE ─────────────────────────────────────────────────────────────
    if "aeneas" not in completed:
        _, history = await _run_stage(
            _translate,
            "Proceed to TRANSLATE. Run Aeneas on the Rust source; fix any "
            "Charon/Aeneas errors by massaging the Rust source as needed." + resume_note,
            deps, history, "TRANSLATE",
        )
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "aeneas" not in completed:
            log.error("TRANSLATE produced no Aeneas output — aborting")
            return "Pipeline aborted: Aeneas translation failed after retries."

    # ── INFER ─────────────────────────────────────────────────────────────────
    if "informal_spec" not in completed:
        _, history = await _run_stage(
            _infer,
            "Proceed to INFER. Call infer_spec to derive the informal specification "
            "from the Lean output and the design document." + resume_note,
            deps, history, "INFER",
        )
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "informal_spec" not in completed:
            log.error("INFER produced no informal spec — aborting")
            return "Pipeline aborted: informal spec inference failed."

    # ── SPEC FORMALISE + SPEC JUDGE + PROVE + PROOF JUDGE ────────────────────
    #
    # Two nested loops:
    #
    #   Outer (spec+proof cycle, cap=5):
    #     Inner (spec formalise+judge, cap=10, stagnation-based):
    #       FORMALISE → SPEC-JUDGE → break on approved/stagnant
    #     PROVE
    #     PROOF-JUDGE → break if no misstated theorems or stagnant
    #               → else feed misstated back into next outer cycle
    #
    # Progress keys:
    #   "verdict"       — latest spec-judge verdict (overwritten each cycle)
    #   "proof_verdict" — latest proof-judge verdict (overwritten each cycle)
    #   Clearing either forces the corresponding loop to re-run on resume.

    _HARD_CAP  = 10   # max spec-judge rounds per cycle
    _CYCLE_CAP = 5    # max full spec→proof cycles

    proof_amendments: list[dict] = []   # mis-stated theorems fed back from proof judge

    for cycle in range(_CYCLE_CAP):

        # ── Phase 1: SPEC FORMALISE + SPEC JUDGE ──────────────────────────────
        # Skip if spec already approved this cycle and no proof amendments arrived.
        if "verdict" not in completed or proof_amendments:
            spec_attempt = 0

            while spec_attempt < _HARD_CAP:
                # Skip FORMALISE only on the very first attempt of the first cycle
                # when resuming mid-run with a passing build and no amendments.
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

                    _, history = await _run_stage(
                        _formalise, formalise_prompt, deps, history,
                        f"FORMALISE (cycle {cycle + 1}, round {spec_attempt + 1})",
                    )
                    _checkpoint(deps)
                    resume_note = ""

                # ── SPEC JUDGE ────────────────────────────────────────────────
                build_ok = deps.progress.get("lean_build", {}).get("success", False)
                sj_result, history = await _run_stage(
                    _judge,
                    f"Proceed to SPEC-JUDGE. Evaluate the theorem statements only "
                    f"(ignore sorry proofs). "
                    f"lake build {'passed ✓' if build_ok else 'FAILED ✗ — approved must be false'}.",
                    deps, history,
                    f"SPEC-JUDGE (cycle {cycle + 1}, round {spec_attempt + 1})",
                )

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

            completed = set(deps.progress.keys())

        # ── Phase 2: PROVE ────────────────────────────────────────────────────
        if "proof_verdict" not in completed:
            _, history = await _run_stage(
                _prove,
                f"Proceed to PROVE (cycle {cycle + 1}). Attempt to fill in proofs "
                "for all sorry theorems in the spec. Do not alter any statement. "
                "Commit the result when done.",
                deps, history,
                f"PROVE (cycle {cycle + 1})",
            )
            _checkpoint(deps)

            # ── PROOF JUDGE ───────────────────────────────────────────────────
            build_ok = deps.progress.get("lean_build", {}).get("success", False)
            pj_result, history = await _run_stage(
                _proof_judge,
                f"Proceed to PROOF-JUDGE (cycle {cycle + 1}). "
                f"lake build {'passed ✓' if build_ok else 'FAILED ✗'}. "
                "Classify every theorem as proved / sorry_acceptable / likely_misstated.",
                deps, history,
                f"PROOF-JUDGE (cycle {cycle + 1})",
            )

            if not (pj_result and pj_result.output):
                log.warning("Proof judge produced no output — stopping")
                break

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
                break
            if pv.stagnant:
                log.warning("Proof judge reports stagnation — accepting current state")
                break

            # Feed mis-stated theorems back to the formaliser next cycle.
            proof_amendments = [t.model_dump() for t in misstated]
            # Clear both verdicts so the next outer cycle re-runs both phases.
            deps.progress.pop("verdict", None)
            deps.progress.pop("proof_verdict", None)
            completed = set(deps.progress.keys())
            log.info(
                "Feeding %d mis-stated theorem(s) back to formaliser for cycle %d",
                len(proof_amendments), cycle + 2,
            )
        else:
            log.info("proof_verdict already in progress — skipping proof stage")
            break

    else:
        log.warning("Spec+proof cycle loop hit cap of %d cycles", _CYCLE_CAP)

    completed = set(deps.progress.keys())

    # ── REPORT ────────────────────────────────────────────────────────────────
    verdict_data     = deps.progress.get("verdict", {})
    proof_verd_data  = deps.progress.get("proof_verdict", {})
    proved_count     = sum(1 for t in proof_verd_data.get("theorems", []) if t.get("status") == "proved")
    sorry_count      = sum(1 for t in proof_verd_data.get("theorems", []) if t.get("status") == "sorry_acceptable")
    misstated_count  = sum(1 for t in proof_verd_data.get("theorems", []) if t.get("status") == "likely_misstated")

    report_result, history = await _run_stage(
        _report,
        f"Proceed to REPORT. "
        f"Spec-judge: approved={verdict_data.get('approved')}, score={verdict_data.get('score')}. "
        f"Proof-judge: proved={proved_count}, sorry_acceptable={sorry_count}, "
        f"likely_misstated={misstated_count}. "
        "Read the artefacts (lean/*.lean, specs/*.json) and produce the full "
        "VERIFICATION_REPORT.md content as your response." + resume_note,
        deps, history, "REPORT",
    )

    # Write and commit from Python — avoids tool-argument validation failures.
    report_text = report_result.output if report_result else ""
    if report_text:
        try:
            tools.write_file(deps, "VERIFICATION_REPORT.md", report_text)
            git_ops.commit(deps.container_id, "stage/report: final pipeline report",
                           glob="VERIFICATION_REPORT.md")
            log.info("Report written and committed")
        except Exception as e:
            log.warning("Could not write report: %s", e)
    else:
        log.warning("REPORT stage produced no output — skipping file write")

    _checkpoint(deps)
    log.info("Pipeline complete")
    return report_text or "(no report generated)"
