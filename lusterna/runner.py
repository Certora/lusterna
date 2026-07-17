"""Generic stage-running machinery: the per-stage prompt briefing, running a stage agent
with history/limits, and the shared tool wiring (the single `bash` tool on the stages that
touch the container). Stage sequencing and the phase logic live in pipeline.py."""
import logging
from typing import Any, Callable

from pydantic_ai import Agent

from . import config, lean, telemetry, tools
from .schemas import AgentDeps
from .stages import translate as _translate, prove as _prove, report as _report

log = logging.getLogger(__name__)


# ── tool registration ───────────────────────────────────────────
# Every stage that touches the container gets ONE tool — `bash` — and drives charon/aeneas/
# cargo/lake and all file/git work itself. TRANSLATE also gets setup_lake_project (build-env
# provisioning that isn't a plain one-liner). The structured stages (EXPLORE / INFER /
# FORMALISE / SPEC-JUDGE / TRANSLATE-JUDGE) are tool-less: inputs injected, structured output
# only — so FORMALISE still cannot smuggle in a proof (bodies are assembled as `sorry`).

for _agent in (_translate, _prove, _report):
    _agent.tool(tools.bash)
_translate.tool(tools.setup_lake_project)


def _pipeline_briefing(deps: AgentDeps) -> str:
    """Return a structured context block describing pipeline state so far.

    Prepended to every stage's runtime prompt so each stage agent understands
    the overall goal, what has been completed, and key findings from prior stages.
    """
    p = deps.progress

    # Completed stages
    stage_flags = [
        ("explore",        "EXPLORE"),
        ("informal_spec",  "INFER"),
        ("aeneas",         "TRANSLATE"),
        ("formal_spec",    "FORMALISE"),
        ("verdict",        "SPEC-JUDGE"),
        ("proofs_done",    "PROVE"),
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
        "Goal: derive and formally verify the properties that the target functions of the "
        "Aeneas-translated crate actually satisfy — the CODE is the source of truth. A property "
        "may be about one function or span several functions and types working together. The "
        "design document (if any) is only a focus hint, not a spec to conform to.",
        f"Completed stages: {', '.join(done) if done else 'none yet'}",
    ]
    if deps.design_doc.strip():
        lines.insert(2, f"Design document (focus hint, excerpt):\n{deps.design_doc[:400].rstrip()}")
    if holes:
        lines.append(f"Aeneas holes (untranslated defs) in the crate: {holes}")
    if fp_line:
        lines.append(fp_line)

    # TRANSLATE accountability trail: scope, opaque assumptions, source modifications.
    trail = p.get("translate_trail")
    if trail:
        tp = p.get("target_patterns") or "whole crate"
        lines.append(f"Translation target scope (Charon --start-from): {tp}")
        opaque = lean.trail_opaque_assumptions(trail)
        if opaque:
            lines.append(
                f"TRANSLATE opaque ASSUMPTIONS (emitted as Lean axioms — the `#print axioms` "
                f"check flags any theorem that depends on them): {opaque}")
        edited = lean.trail_refactored_paths(trail)
        if edited:
            lines.append(
                f"⚠ SOURCE MODIFIED during TRANSLATE — behaviour-preserving refactor(s) to "
                f"{edited}. The translation is NOT a verbatim image of the original for those "
                f"items; properties touching them are verified of the REFACTORED code "
                f"(behaviour-equivalence asserted + cross-checked by proofs, not machine-certified). "
                f"See translate/accountability.md.")

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
        ("informal_spec", "specs/informal_spec.json"),
        ("formal_spec",   lean.impl_spec(deps)),
    ]:
        if key in p and path:
            artefacts.append(path)
    if artefacts:
        lines.append(
            f"Key artefacts (all in /workspace/out — read with bash `cat`): "
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
