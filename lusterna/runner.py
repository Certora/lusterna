"""Generic stage-running machinery: the per-stage prompt briefing, running a stage agent
with history/limits, and the shared tool wiring (the single `bash` tool on the stages that
touch the container). Stage sequencing and the phase logic live in pipeline.py."""
import json
import logging
import uuid
from typing import Any, Callable

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import UsageLimits

from . import config, container, lean, telemetry, tools
from .schemas import AgentDeps
from .stages import (
    explore as _explore, infer as _infer, translate as _translate,
    translate_judge as _translate_judge, formalise as _formalise,
    prove as _prove, report as _report,
)

log = logging.getLogger(__name__)


# ── tool registration ───────────────────────────────────────────
# Every stage gets ONE tool — `bash` — and reads/writes/searches and drives charon/aeneas/cargo/
# lake itself, so nothing is injected but small hints and computed facts (no dumping repo sources
# or whole translations into the prompt). TRANSLATE also gets setup_lake_project (build-env
# provisioning that isn't a plain one-liner). FORMALISE gets bash too — it must navigate a large
# translation to reference exact mangled names/signatures, so it READS the crate selectively
# (grep/sed on /workspace/out/lean) rather than having ~9k lines injected; it still cannot smuggle
# a proof because its FormalSpec output has no proof field (bodies are assembled as `sorry`).
# SPEC-JUDGE stays injected (it reasons over the small assembled spec).

for _agent in (_explore, _infer, _translate, _translate_judge, _formalise, _prove, _report):
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
                     stop_check: Callable[[AgentDeps, Any], bool] | None = None) -> Any:
    """Run one stage agent. Each stage starts with no prior history.

    Stages communicate via the filesystem and deps.progress, not via conversation
    context — so no history is passed in or accumulated across stages. When stop_check
    is given it is polled on each graph node (deps, node); returning True ends the run early.
    There is no per-stage request cap — the session token budget (enforced in factory's
    before_model_request hook) is the resource guard. This must be set EXPLICITLY: pydantic-ai
    otherwise imposes a default request_limit of 50, which silently truncates a stage mid-work
    (e.g. PROVE while it is still discovering the Aeneas lemmas it needs). Re-raises
    UnexpectedModelBehavior; judge stages catch it locally.
    """
    log.info("─── Stage: %s ───", label)
    telemetry.stage.reset()
    deps.message_history = []
    full_prompt = _pipeline_briefing(deps) + prompt
    async with agent.iter(full_prompt, deps=deps,
                          usage_limits=UsageLimits(request_limit=None)) as run:
        async for node in run:
            if stop_check and stop_check(deps, node):
                break
        result = run.result
    log.info(
        "Stage %s complete — session total=%d/%s",
        label, telemetry.session.total(), telemetry.budget or "∞",
    )
    return result


def _log_cc_tool(stage: str, name: str, inp: dict) -> None:
    """Render one CC tool_use as a concise trail line in the HOST log — the user's live window
    into what the in-container session is doing (mirrors the old bash-tool trail)."""
    if name == "Bash":
        log.info("[%s] $ %s", stage, " ".join((inp.get("command") or "").split())[:200])
    elif name in ("Write", "Edit", "Read", "NotebookEdit"):
        log.info("[%s] %s %s", stage, name, inp.get("file_path", ""))
    elif name in ("Glob", "Grep"):
        log.info("[%s] %s %s", stage, name, (inp.get("pattern") or "")[:80])
    elif name == "TodoWrite":
        log.info("[%s] plan: %d todo(s)", stage, len(inp.get("todos") or []))
    else:
        log.info("[%s] %s", stage, name)


def run_cc_stage(
    deps: AgentDeps, *, stage: str, prompt: str, briefing: str | None = None,
    allowed_tools: str = "Bash,Edit,Write,Read,Glob,Grep,TodoWrite",
    model: str | None = None, effort: str | None = None,
    max_budget_usd: float | None = None, resume_sid: str | None = None,
    timeout: int = 3600,
) -> dict:
    """Run one pipeline stage as a headless Claude Code session inside the container, STREAMING its
    activity to the host log live (the user's window — the harness runs outside the container).

    First call: pass `briefing` (written to a file, applied via --append-system-prompt-file) and a
    fresh session id is minted. Re-invocation of the SAME stage (gate feedback, §4 hybrid-resume):
    pass `resume_sid` and NO briefing — the resumed session keeps its system prompt + full context,
    and `prompt` carries the gate's feedback.

    Uses --output-format stream-json --verbose; each event is parsed as it arrives (tool_use →
    trail line, assistant text → narration) and the final `result` event is returned
    {session_id, total_cost_usd, subtype, num_turns, …}. Session id → deps.progress['cc_sessions'].
    Autonomy: dontAsk + allowlist (bypassPermissions is refused as root); --max-budget-usd is the
    runaway backstop. Raises UnexpectedModelBehavior if no result event is produced.
    """
    resuming = resume_sid is not None
    sid = resume_sid or str(uuid.uuid4())
    log.info("─── Stage: %s (Claude Code%s) ───", stage, " · resume" if resuming else "")
    telemetry.stage.reset()

    argv = ["claude", "-p"]
    if resuming:
        argv += ["--resume", sid]
    else:
        briefing_path = f"/workspace/.lusterna/{stage.lower()}-briefing.md"
        container.write_file(deps.container_id, briefing_path, briefing or "")
        argv += ["--session-id", sid, "--append-system-prompt-file", briefing_path]
    argv += ["--output-format", "stream-json", "--verbose",
             "--permission-mode", "dontAsk", "--allowedTools", allowed_tools,
             "--model", model or config.CC_MODEL]
    if effort:
        argv += ["--effort", effort]
    if max_budget_usd:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    argv.append(prompt)

    captured: dict = {}

    def on_line(line: str) -> None:
        if not line.strip():
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        t = ev.get("type")
        if t == "assistant":
            for block in ev.get("message", {}).get("content", []):
                bt = block.get("type")
                if bt == "tool_use":
                    _log_cc_tool(stage, block.get("name", "?"), block.get("input") or {})
                elif bt == "text" and block.get("text", "").strip():
                    log.info("[%s] %s", stage, " ".join(block["text"].split())[:200])
        elif t == "result":
            captured.update(ev)

    code, _out, err = container.exec_stream(
        deps.container_id, argv, "/workspace", on_line,
        passthrough_env=["ANTHROPIC_API_KEY"], timeout=timeout)
    deps.progress.setdefault("cc_sessions", {})[stage] = sid

    if code == 124:
        log.warning("CC stage %s: timeout after %ds — the in-container session %s persists for "
                    "resume", stage, timeout, sid[:8])
    if not captured:
        log.error("CC stage %s produced no result event (exit=%s). stderr tail: %s",
                  stage, code, (err or "")[-500:])
        raise UnexpectedModelBehavior(f"{stage}: Claude Code returned no result event")

    cost = captured.get("total_cost_usd") or 0.0
    costs = deps.progress.setdefault("cc_costs", {})
    costs[stage] = costs.get(stage, 0.0) + cost
    log.info("Stage %s complete — session=%s cost=$%.4f subtype=%s turns=%s",
             stage, sid[:8], cost, captured.get("subtype"), captured.get("num_turns"))
    if captured.get("is_error") or captured.get("subtype") != "success":
        log.warning("CC stage %s ended non-success (subtype=%s) — the harness gate on the produced "
                    "artefacts is authoritative regardless", stage, captured.get("subtype"))
    return captured
