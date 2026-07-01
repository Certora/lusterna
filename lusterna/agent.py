"""Orchestrator agent — drives the full Rust → Lean verification pipeline."""
import logging

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.models import ModelRequestContext

from . import checkpoint, compaction, git_ops, subagents, tools
from .state import AgentDeps
from . import config

log = logging.getLogger(__name__)

# ── message-history snapshot hook ─────────────────────────────────────────────
# Before every model request, the hook captures all messages seen so far into
# deps.message_history. This ensures mid-run _checkpoint() calls include the
# conversation up to the current point, not just the empty list from session start.

_hooks = Hooks()


@_hooks.on.before_model_request
async def _snapshot_messages(
    ctx: RunContext[AgentDeps], model_ctx: ModelRequestContext
) -> ModelRequestContext | None:
    ctx.deps.message_history = list(model_ctx.messages)
    return model_ctx


# ── orchestrator agent ────────────────────────────────────────────────────────

orchestrator = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    instructions="""
You are Lusterna, a formal verification orchestrator.
Your job is to translate a Rust codebase into Lean 4 and produce a formally verified specification.

Pipeline stages (execute in order, committing artefacts after each):
1. EXPLORE  — list source files, read key ones, query the RAG knowledge base for relevant context.
2. TRANSLATE — call run_aeneas to produce Lean 4 output.  Interpret the result:
   a. success=true  → proceed.
   b. partial=true  → Aeneas translated some functions but failed on others.
      Read aeneas_errors carefully.  Common fixable causes:
        - "Could not translate ... main" with vec!/println! → rewrite src/main.rs
          to remove or stub-out the main body (keep only the functions you need).
        - Unsupported alloc/std constructs → replace with simple stubs or remove.
      Call write_rust_file to apply the fix, then call run_aeneas again (up to 2 retries).
      Accept the partial output if you cannot remove all errors.
   c. success=false AND lean_files=[] → translation completely failed.
      Try to fix the Rust source (up to 2 retries).  Only if still no output:
      write a faithful manual Lean translation using write_file, commit it, and
      note in the report that automatic translation failed.
3. INFER    — call infer_spec to derive an informal specification from the Lean code + design doc.
4. FORMALISE + BUILD LOOP — repeat until lake build passes (max 3 attempts):
   a. Call formalise_spec to produce Lean 4 theorem stubs.
   b. Call check_lean to build the spec with lake. Read stdout/stderr carefully.
   c. If build fails: read the error, fix the Lean file(s) with write_file, then go to (b).
      Common fixes: add missing imports, fix type errors, adjust namespace, register the
      spec file in the lakefile if it is not yet declared as a library target.
   d. Only proceed to JUDGE once lake build succeeds, or after 3 failed attempts
      (in which case note the build failure explicitly in the judge call).
5. JUDGE    — call judge_spec once. If approved=true OR score >= 7, proceed immediately to REPORT.
             Only if approved=false AND score < 7: go back to step 4 (formalise + build loop),
             then judge_spec once more. Do not judge more than twice total.
6. REPORT   — write a final report summarising all artefacts and open proof obligations.

Use git_commit to record every significant artefact.
Use rag_query whenever you need domain knowledge about Rust, Lean, or Aeneas.
""",
    capabilities=[_hooks],
)


# ── tool registrations ────────────────────────────────────────────────────────

@orchestrator.tool
def read_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a source file from the target Rust repository (relative path, e.g. 'src/main.rs')."""
    return tools.read_file(ctx.deps, path)


@orchestrator.tool
def read_output_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a generated file from the output directory (relative path, e.g. 'lean/main.lean')."""
    return tools.read_output_file(ctx.deps, path)


@orchestrator.tool
def write_file(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    """Write content to a file in the work directory."""
    return tools.write_file(ctx.deps, path, content)


@orchestrator.tool
def write_rust_file(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    """Overwrite a Rust source file in the repo (repo-relative path).

    Use to remove untranslatable constructs (vec!, println!, main body, etc.)
    before retrying run_aeneas.
    """
    return tools.write_rust_file(ctx.deps, path, content)


@orchestrator.tool
def list_files(ctx: RunContext[AgentDeps], extension: str = "rs") -> list[str]:
    """List files in the target repository with the given extension (e.g. 'rs', 'toml')."""
    return tools.list_files(ctx.deps, extension)


@orchestrator.tool
def run_aeneas(ctx: RunContext[AgentDeps], entry_file: str) -> dict:
    """Translate entry_file (Rust) to Lean 4 using Aeneas. Returns lean_path and commit SHA."""
    result = tools.run_aeneas(ctx.deps, entry_file)
    ctx.deps.progress["aeneas"] = result
    _checkpoint(ctx.deps)
    return result


@orchestrator.tool
async def infer_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Infer the informal specification from the translated Lean code and design doc."""
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    lean_path = ctx.deps.progress.get("aeneas", {}).get("lean_path")
    lean_code = tools.read_output_file(ctx.deps, lean_path) if lean_path else "(no Lean code yet)"
    try:
        spec = await subagents.infer_informal_spec(lean_code, ctx.deps.design_doc)
    except UnexpectedModelBehavior as e:
        return {"error": f"infer_spec subagent failed: {e}"}
    ctx.deps.progress["informal_spec"] = spec.model_dump()
    tools.write_file(ctx.deps, "specs/informal_spec.json", spec.model_dump_json(indent=2))
    git_ops.commit(ctx.deps.container_id, "feat(spec): informal specification", glob="specs/")
    _checkpoint(ctx.deps)
    return spec.model_dump()


@orchestrator.tool
async def formalise_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Derive a formal Lean 4 specification from the informal spec."""
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from .subagents import InformalSpec
    inf_data = ctx.deps.progress.get("informal_spec")
    if not inf_data:
        raise ValueError("infer_spec must be called before formalise_spec")
    informal = InformalSpec(**inf_data)
    lean_path = ctx.deps.progress.get("aeneas", {}).get("lean_path")
    lean_code = tools.read_output_file(ctx.deps, lean_path) if lean_path else ""
    try:
        formal = await subagents.derive_formal_spec(informal, lean_code)
    except UnexpectedModelBehavior as e:
        return {"error": f"formalise_spec subagent failed: {e} — try calling formalise_spec again or write the spec manually with write_file"}
    ctx.deps.progress["formal_spec"] = formal.model_dump()
    tools.write_file(ctx.deps, "specs/formal_spec.lean", formal.lean_definitions + "\n\n" + formal.lean_theorem_stubs)
    git_ops.commit(ctx.deps.container_id, "feat(spec): formal specification stubs", glob="specs/")
    _checkpoint(ctx.deps)
    return formal.model_dump()


@orchestrator.tool
async def judge_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Have a judge subagent evaluate the formal specification. Returns verdict.

    The judge is given the lake build result from the most recent check_lean call.
    A failing build forces approved=False and score≤4.
    """
    from .subagents import InformalSpec, FormalSpec
    inf_data = ctx.deps.progress.get("informal_spec")
    frm_data = ctx.deps.progress.get("formal_spec")
    if not inf_data or not frm_data:
        raise ValueError("infer_spec and formalise_spec must be called first")
    informal = InformalSpec(**inf_data)
    formal = FormalSpec(**frm_data)
    rs_files = tools.list_files(ctx.deps, "rs")
    rust_src = "\n\n".join(tools.read_file(ctx.deps, f) for f in rs_files[:5])
    build_result = ctx.deps.progress.get("lean_build", {"success": False, "stdout": "", "stderr": "check_lean not called"})
    verdict = await subagents.judge_formal_spec(informal, formal, rust_src, build_result)
    ctx.deps.progress["verdict"] = verdict.model_dump()
    log.info("Judge verdict: approved=%s score=%d", verdict.approved, verdict.score)
    _checkpoint(ctx.deps)
    return verdict.model_dump()


@orchestrator.tool
def rag_query(ctx: RunContext[AgentDeps], query: str, top_k: int = 5) -> list[dict]:
    """Query the local RAG knowledge base for domain knowledge."""
    return tools.rag_query(query, top_k=top_k)


@orchestrator.tool
def git_commit(ctx: RunContext[AgentDeps], message: str) -> str:
    """Stage all pending changes in the work directory and commit."""
    return tools.git_commit(ctx.deps, message)


@orchestrator.tool
def git_log(ctx: RunContext[AgentDeps], n: int = 10) -> str:
    """Return the last n git commits in the work directory."""
    return tools.git_log(ctx.deps, n=n)


@orchestrator.tool
def check_lean(ctx: RunContext[AgentDeps], lean_file: str) -> dict:
    """Run `lake build` on the Lean project and return {success, stdout, stderr}.

    Always call this after formalise_spec and after any manual Lean edits.
    The result is stored and forwarded to judge_spec automatically.
    """
    result = tools.check_lean(ctx.deps, lean_file)
    ctx.deps.progress["lean_build"] = result
    _checkpoint(ctx.deps)
    return result


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


# ── main entry point ──────────────────────────────────────────────────────────

async def run_session(deps: AgentDeps) -> str:
    """Drive the full pipeline and return a final summary string."""
    resuming = bool(deps.message_history)

    if resuming:
        # Full conversation history restored — the agent remembers everything.
        # Inject a brief note as the new user turn so the agent knows the container
        # is fresh and it should continue from where it stopped.
        initial_prompt = (
            "SESSION RESUMED — the Docker container is fresh but all previously "
            "generated artefacts have been restored into it. Your full conversation "
            "history above shows exactly what was done. Continue from where you left off."
        )
        log.info("Resuming session with %d messages of history", len(deps.message_history))
    else:
        initial_prompt = (
            f"Begin the formal verification pipeline for the Rust repository at {deps.repo_path}.\n"
            f"Design document (excerpt):\n{deps.design_doc[:2000]}\n"
            "Follow the pipeline stages defined in your instructions."
        )

    result = None
    async with orchestrator.iter(
        initial_prompt,
        deps=deps,
        message_history=deps.message_history,
    ) as agent_run:
        async for node in agent_run:
            if compaction.needs_compaction(deps.message_history):
                log.info("Context threshold reached — compacting")
                deps.message_history = await compaction.compact(
                    deps.message_history,
                    summarise_fn=subagents.summarise_history,
                )

        result = agent_run.result

    if result:
        # Capture the full conversation so the next checkpoint (or final save)
        # includes everything the agent saw and generated this session.
        deps.message_history = result.all_messages()
        _checkpoint(deps)

    summary = result.output if result else "(no output)"
    log.info("Pipeline complete")
    return summary
