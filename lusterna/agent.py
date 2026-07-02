"""Stage-based pipeline: one Agent per stage, Python drives sequencing and loops."""
import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import ModelRequestContext

from . import checkpoint, compaction, git_ops, subagents, tools
from .subagents import InformalSpec, FormalSpec, JudgeVerdict
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


def rag_query(ctx: RunContext[AgentDeps], query: str, top_k: int = 5) -> list[dict]:
    """Query the local RAG knowledge base for Lean / Aeneas domain knowledge."""
    return tools.rag_query(query, top_k=top_k)


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
- Query the RAG knowledge base for relevant Lean/Aeneas domain context.
- Identify functions to be translated and flag obvious Aeneas incompatibilities
  (vec!, println!, trait objects, unsupported std types, etc.).

Write a short structured summary of findings. Then stop — do not run Aeneas.
""",
)
_explore.tool(list_files)
_explore.tool(read_file)
_explore.tool(rag_query)


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
""",
)
_translate.tool(list_files)
_translate.tool(read_file)
_translate.tool(read_output_file)
_translate.tool(write_rust_file)
_translate.tool(run_aeneas)
_translate.tool(rag_query)
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
_infer.tool(rag_query)


_formalise = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    capabilities=[_hooks],
    instructions="""
You are the FORMALISE+BUILD stage of the Lusterna pipeline.

Produce a Lean 4 formal specification that compiles with `lake build`:

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

Do NOT call judge_spec — that is handled by a separate stage.
""",
)
_formalise.tool(list_files)
_formalise.tool(read_file)
_formalise.tool(read_output_file)
_formalise.tool(write_file)
_formalise.tool(formalise_spec)
_formalise.tool(check_lean)
_formalise.tool(rag_query)
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
_judge.tool(read_file)


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

    # ── FORMALISE + JUDGE loop ────────────────────────────────────────────────
    # Termination is stagnation-based, not count-based:
    #   • Success : approved=True or score >= 7
    #   • Stagnation : the set of failing component names is unchanged from the
    #                  previous round (model saw the same feedback, produced the
    #                  same broken components — more rounds won't help), OR the
    #                  score decreased (formaliser broke something it had fixed).
    #   • Safety net: absolute cap of 10 rounds against infinite loops.
    _HARD_CAP = 10

    if "verdict" not in completed:
        attempt = 0

        while attempt < _HARD_CAP:
            # On the first attempt when resuming mid-formalise (lean_build already
            # succeeded and formal_spec exists), skip straight to JUDGE.
            skip_formalise = (
                attempt == 0
                and "formal_spec" in completed
                and deps.progress.get("lean_build", {}).get("success")
            )

            if not skip_formalise:
                if attempt == 0:
                    formalise_prompt = (
                        "Proceed to FORMALISE+BUILD. Call formalise_spec, write the spec "
                        "file to lean/, register it in the lakefile, then call check_lean "
                        "until the build passes (max 3 build attempts)." + resume_note
                    )
                else:
                    # Build a focused prompt listing only the failing components.
                    prev_verdict_data = deps.progress.get("verdict", {})
                    failing_comps = [
                        c for c in prev_verdict_data.get("components", [])
                        if not c.get("approved")
                    ]
                    if failing_comps:
                        component_lines = "\n".join(
                            f"  - {c['name']} ({c['kind']}): "
                            + ("; ".join(c.get("issues", [])) or "no details")
                            for c in failing_comps
                        )
                        formalise_prompt = (
                            f"The judge did not approve (round {attempt + 1}). "
                            f"The following {len(failing_comps)} component(s) were rejected — "
                            "fix only these, leave approved components untouched:\n"
                            f"{component_lines}\n\n"
                            "After editing the spec file call check_lean to confirm "
                            "the build still passes."
                        )
                    else:
                        formalise_prompt = (
                            f"The judge did not approve (round {attempt + 1}). "
                            "Revise the formal specification based on the judge's overall "
                            "feedback above, then call check_lean to confirm the build passes."
                        )
                _, history = await _run_stage(
                    _formalise, formalise_prompt, deps, history,
                    f"FORMALISE (round {attempt + 1})",
                )
                _checkpoint(deps)
                resume_note = ""

            # ── JUDGE ─────────────────────────────────────────────────────────
            build_ok = deps.progress.get("lean_build", {}).get("success", False)
            judge_prompt = (
                f"Proceed to JUDGE. Evaluate the formal specification. "
                f"lake build {'passed ✓' if build_ok else 'FAILED ✗ — approved must be false'}."
            )
            verdict_result, history = await _run_stage(
                _judge, judge_prompt, deps, history,
                f"JUDGE (round {attempt + 1})",
            )

            if not (verdict_result and verdict_result.output):
                log.warning("Judge produced no output in round %d — stopping loop", attempt + 1)
                break

            verdict: JudgeVerdict = verdict_result.output
            deps.progress["verdict"] = verdict.model_dump()
            failing_names = sorted(c.name for c in verdict.components if not c.approved)
            log.info(
                "Judge round %d: approved=%s score=%d components=%d failing=%s stagnant=%s",
                attempt + 1, verdict.approved, verdict.score,
                len(verdict.components), failing_names or "none", verdict.stagnant,
            )
            _checkpoint(deps)

            # ── termination checks ─────────────────────────────────────────
            if verdict.approved or verdict.score >= 7:
                log.info("Verdict accepted — exiting loop")
                break

            if verdict.stagnant:
                log.warning(
                    "Judge reports stagnation on round %d (failing: %s) — stopping loop",
                    attempt + 1, failing_names,
                )
                break

            attempt += 1

        else:
            log.warning("FORMALISE+JUDGE loop hit hard cap of %d rounds", _HARD_CAP)

        completed = set(deps.progress.keys())

    # ── REPORT ────────────────────────────────────────────────────────────────
    verdict_data = deps.progress.get("verdict", {})
    report_result, history = await _run_stage(
        _report,
        f"Proceed to REPORT. "
        f"Judge verdict: approved={verdict_data.get('approved')}, "
        f"score={verdict_data.get('score')}. "
        "Read the artefacts (lean/*.lean, specs/*.json, specs/*.lean) and "
        "produce the full VERIFICATION_REPORT.md content as your response." + resume_note,
        deps, history, "REPORT",
    )

    # Write and commit the report from Python — avoids the model having to
    # pass the full report text as a tool argument (which triggers arg validation
    # failures when the model forgets to include the content field).
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
