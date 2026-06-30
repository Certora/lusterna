"""Orchestrator agent — drives the full Rust → Lean verification pipeline."""
import logging

from pydantic_ai import Agent, RunContext

from . import checkpoint, compaction, git_ops, subagents, tools
from .state import AgentDeps
from . import config

log = logging.getLogger(__name__)

# ── orchestrator agent ────────────────────────────────────────────────────────

orchestrator = Agent(
    config.MODEL,
    deps_type=AgentDeps,
    instructions="""
You are Lusterna, a formal verification orchestrator.
Your job is to translate a Rust codebase into Lean 4 and produce a formally verified specification.

Pipeline stages (execute in order, committing artefacts after each):
1. EXPLORE  — list source files, read key ones, query the RAG knowledge base for relevant context.
2. TRANSLATE — call run_aeneas on the entry point to produce Lean 4 output.
3. INFER    — call infer_spec to derive an informal specification from the Lean code + design doc.
4. FORMALISE — call formalise_spec to turn the informal spec into Lean 4 theorem stubs.
5. JUDGE    — call judge_spec to evaluate the formal spec; if score < 7, revise and re-judge.
6. REPORT   — write a final report summarising all artefacts and open proof obligations.

Use git_commit to record every significant artefact.
Use rag_query whenever you need domain knowledge about Rust, Lean, or Aeneas.
""",
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
    lean_path = ctx.deps.progress.get("aeneas", {}).get("lean_path")
    lean_code = tools.read_output_file(ctx.deps, lean_path) if lean_path else "(no Lean code yet)"
    spec = await subagents.infer_informal_spec(lean_code, ctx.deps.design_doc)
    ctx.deps.progress["informal_spec"] = spec.model_dump()
    tools.write_file(ctx.deps, "specs/informal_spec.json", spec.model_dump_json(indent=2))
    git_ops.commit(ctx.deps.container_id, "feat(spec): informal specification", glob="specs/")
    _checkpoint(ctx.deps)
    return spec.model_dump()


@orchestrator.tool
async def formalise_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Derive a formal Lean 4 specification from the informal spec."""
    from .subagents import InformalSpec
    inf_data = ctx.deps.progress.get("informal_spec")
    if not inf_data:
        raise ValueError("infer_spec must be called before formalise_spec")
    informal = InformalSpec(**inf_data)
    lean_path = ctx.deps.progress.get("aeneas", {}).get("lean_path")
    lean_code = tools.read_output_file(ctx.deps, lean_path) if lean_path else ""
    formal = await subagents.derive_formal_spec(informal, lean_code)
    ctx.deps.progress["formal_spec"] = formal.model_dump()
    tools.write_file(ctx.deps, "specs/formal_spec.lean", formal.lean_definitions + "\n\n" + formal.lean_theorem_stubs)
    git_ops.commit(ctx.deps.container_id, "feat(spec): formal specification stubs", glob="specs/")
    _checkpoint(ctx.deps)
    return formal.model_dump()


@orchestrator.tool
async def judge_spec(ctx: RunContext[AgentDeps]) -> dict:
    """Have a judge subagent evaluate the formal specification. Returns verdict."""
    from .subagents import InformalSpec, FormalSpec
    inf_data = ctx.deps.progress.get("informal_spec")
    frm_data = ctx.deps.progress.get("formal_spec")
    if not inf_data or not frm_data:
        raise ValueError("infer_spec and formalise_spec must be called first")
    informal = InformalSpec(**inf_data)
    formal = FormalSpec(**frm_data)
    rs_files = tools.list_files(ctx.deps, "rs")
    rust_src = "\n\n".join(tools.read_file(ctx.deps, f) for f in rs_files[:5])
    verdict = await subagents.judge_formal_spec(informal, formal, rust_src)
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
    """Run `lake build` on a Lean file and return {success, stdout, stderr}."""
    return tools.check_lean(ctx.deps, lean_file)


# ── internal helpers ──────────────────────────────────────────────────────────

def _checkpoint(deps: AgentDeps) -> None:
    checkpoint.save(deps.session_id, {
        "repo_path": str(deps.repo_path),
        "work_path": str(deps.work_path),
        "container_id": deps.container_id,
        "design_doc": deps.design_doc,
        "progress": deps.progress,
    })


# ── main entry point ──────────────────────────────────────────────────────────

async def run_session(deps: AgentDeps) -> str:
    """Drive the full pipeline and return a final summary string."""
    initial_prompt = (
        f"Begin the formal verification pipeline for the Rust repository at {deps.repo_path}.\n"
        f"Design document (excerpt):\n{deps.design_doc[:2000]}\n"
        "Follow the pipeline stages defined in your instructions."
    )

    message_history = []
    result = None
    async with orchestrator.iter(initial_prompt, deps=deps, message_history=message_history) as agent_run:
        async for node in agent_run:
            # Compact context if the history is getting large
            if compaction.needs_compaction(message_history):
                log.info("Context threshold reached — compacting")
                message_history = await compaction.compact(
                    message_history,
                    summarise_fn=subagents.summarise_history,
                )

        result = agent_run.result

    summary = result.output if result else "(no output)"
    log.info("Pipeline complete")
    return summary
