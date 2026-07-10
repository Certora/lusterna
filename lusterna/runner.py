"""Generic stage-running machinery: the per-stage prompt briefing, running a stage agent
with history/limits, and the shared tool wiring (the check_lean wrapper + tool registration
on the agents that take tools). Stage sequencing and the phase logic live in pipeline.py."""
import logging
from typing import Any, Callable

from pydantic_ai import Agent, RunContext

from . import checkpoint, config, lean, telemetry, tools
from .schemas import AgentDeps
from .stages import prove as _prove, report as _report

log = logging.getLogger(__name__)


def check_lean(ctx: RunContext[AgentDeps], lean_file: str) -> dict:
    """Run `lake build` in the Lean project and return {success, stderr}.

    The result is stored in progress['lean_build'] and a checkpoint is saved.
    Always call this after writing or modifying any Lean file. On a SUCCESSFUL build this also
    snapshots the compiling impl-spec if it reached a new `sorry` minimum (PROVE's best-state
    preservation — see _record_prove_best); only build-verified states ever count as progress.
    """
    result = lean.check_lean(ctx.deps, lean_file)
    ctx.deps.progress["lean_build"] = result
    ctx.deps.progress["build_seq"] = ctx.deps.progress.get("build_seq", 0) + 1
    if result.get("success"):
        lean.record_prove_best(ctx.deps)
    checkpoint.snapshot(ctx.deps)
    return result


# ── tool registration ───────────────────────────────────────────
# Stage agents are declared in stages.py; their tools — which depend on the
# orchestration helpers in this module — are attached here. TRANSLATE is NOT an
# agent: it runs Charon+Aeneas mechanically on the untouched source (see
# _run_translate_stages), so nothing can rewrite the code under verification.

# FORMALISE is now a structured, tool-less stage: the orchestrator injects its inputs and
# receives a FormalSpec (statements only), then assembles + builds the .lean itself. This
# removes the free-form build loop where the agent used to (against instructions) write and
# debug proofs — proofs are now structurally impossible until PROVE.

_prove.tool(tools.list_files)
_prove.tool(tools.search_output_file)
_prove.tool(tools.read_output_lines)
_prove.tool(tools.read_output_file)
_prove.tool(tools.patch_output_lines)
_prove.tool(tools.write_file)
_prove.tool(check_lean)
_prove.tool(tools.git_commit)
_prove.tool(tools.git_log)

_report.tool(tools.write_file)
_report.tool(tools.git_log)


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
        ("reconciliation",         "RECONCILE"),
        ("proofs_done",            "PROVE"),
    ]
    done = [label for key, label in stage_flags if key in p]

    fp = p.get("footprint")
    fp_line = ""
    if fp is not None:
        hif = fp.get("holes_in_footprint", [])
        fp_line = (
            f"Property footprint: {len(fp.get('defs', []))} translation def(s); "
            + ("no Aeneas holes inside — proven properties are soundly grounded"
               if not hif else
               f"⚠ {len(hif)} untranslated hole(s) INSIDE the footprint ({hif}) — "
               f"properties touching them are NOT soundly grounded")
        )
    holes = (p.get("aeneas") or {}).get("holes", [])

    lines = [
        "## Pipeline context",
        "Goal: formally verify the properties the design document describes against the "
        "Aeneas-translated crate. What to specify is the tool's choice; a property may be "
        "about one function or span several functions and types. The source is never "
        "modified, so the translation is a faithful image of the real code.",
        f"Design document (excerpt):\n{deps.design_doc[:600].rstrip()}",
        "",
        f"Completed stages: {', '.join(done) if done else 'none yet'}",
    ]
    if holes:
        lines.append(f"Aeneas holes (untranslated defs) in the crate: {holes}")
    if fp_line:
        lines.append(fp_line)
    ax = p.get("axioms")
    if ax is not None:
        tainted = ax.get("tainted", [])
        lines.append(
            f"Axiom check (Lean `#print axioms`, authoritative): "
            f"{len(ax.get('clean', []))} theorem(s) genuinely established (no sorryAx), "
            f"{len(tainted)} still resting on sorry"
            + (f" — tainted: {tainted}" if tainted else "")
        )

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
        ("formal_spec",            lean.impl_spec(deps)),
    ]:
        if key in p and path:
            artefacts.append(path)
    if "reconciliation" in p:
        artefacts.append("specs/reconciliation.json")
    if artefacts:
        lines.append(
            f"Key artefacts (all in /workspace/out — use read_output_file): "
            + ", ".join(artefacts)
        )

    # Spec-judge verdict
    verdict = p.get("verdict")
    if verdict is not None:
        defects = verdict.get("defects", [])
        if defects:
            lines.append(
                f"\nSpec-judge: {len(defects)} open defect(s): "
                + ", ".join(f"{d['theorem']}[{d['kind']}]" for d in defects)
            )
        else:
            lines.append("\nSpec-judge: no defects (spec approved ✓)")

    # Reconciliation summary
    rc = p.get("reconciliation")
    if rc:
        discrepancies = rc.get("discrepancies", [])
        critical = [d for d in discrepancies if d.get("severity") == "critical"]
        obligations = rc.get("refinement_obligations", [])
        lines.append(
            f"\nReconciliation: {len(discrepancies)} discrepancy/ies "
            f"({len(critical)} critical), {len(obligations)} refinement obligation(s)"
        )
        for d in critical:
            lines.append(f"  [CRITICAL {d.get('kind','')}] {d.get('description','')[:120]}")

    lines.append("")   # trailing newline before stage-specific prompt
    return "\n".join(lines) + "\n"


async def _run_stage(agent: Agent, prompt: str, deps: AgentDeps, label: str,
                     stop_check: Callable[[AgentDeps, Any], bool] | None = None,
                     request_limit: Any = "default") -> Any:
    """Run one stage agent. Each stage starts with no prior history.

    Stages communicate via the filesystem and deps.progress, not via conversation
    context — so no history is passed in or accumulated across stages. When stop_check
    is given it is polled on each graph node (deps, node); returning True ends the run early.
    `request_limit` overrides config.REQUEST_LIMIT when not "default".
    Re-raises UnexpectedModelBehavior; judge stages catch it locally.
    """
    log.info("─── Stage: %s ───", label)
    telemetry.stage.reset()
    deps.message_history = []
    full_prompt = _pipeline_briefing(deps) + prompt
    from pydantic_ai.usage import UsageLimits
    rl = config.REQUEST_LIMIT if request_limit == "default" else request_limit
    async with agent.iter(
        full_prompt, deps=deps,
        usage_limits=UsageLimits(request_limit=rl),
    ) as run:
        async for node in run:
            if stop_check and stop_check(deps, node):
                break
        result = run.result
    log.info(
        "Stage %s complete — session total=%d/%s",
        label, telemetry.session.total(), telemetry.budget or "∞",
    )
    return result
