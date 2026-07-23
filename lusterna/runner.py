"""The stage runner: launch one pipeline stage as a headless Claude Code session inside the
container and stream its activity to the host log live. The pipeline (pipeline.py) sequences these
and applies the trusted mechanical gates to the files each session leaves behind."""
import json
import logging
import uuid

from . import config, container
from .schemas import AgentDeps

log = logging.getLogger(__name__)


class StageFailed(Exception):
    """A spawned Claude Code stage session returned no usable result (no `result` event). The
    pipeline catches this and stops gracefully — the on-disk artefacts + gates are authoritative."""


def _log_cc_tool(stage: str, name: str, inp: dict) -> None:
    """Render one CC tool_use as a concise trail line in the HOST log — the user's live window into
    what the in-container session is doing (mirrors the old bash-tool trail)."""
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
    fresh session id is minted. Re-invocation of the SAME stage (gate feedback / hybrid-resume): pass
    `resume_sid` and NO briefing — the resumed session keeps its system prompt + full context, and
    `prompt` carries the gate's feedback.

    Uses --output-format stream-json --verbose; each event is parsed as it arrives (tool_use → trail
    line, assistant text → narration) and the final `result` event is returned
    {session_id, total_cost_usd, subtype, num_turns, …}. Session id → deps.progress['cc_sessions'].
    Autonomy: dontAsk + allowlist (bypassPermissions is refused as root); --max-budget-usd is the
    runaway backstop. Raises UnexpectedModelBehavior if no result event is produced.
    """
    resuming = resume_sid is not None
    sid = resume_sid or str(uuid.uuid4())
    log.info("─── Stage: %s (Claude Code%s) ───", stage, " · resume" if resuming else "")

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
        raise StageFailed(f"{stage}: Claude Code returned no result event")

    cost = captured.get("total_cost_usd") or 0.0
    costs = deps.progress.setdefault("cc_costs", {})
    costs[stage] = costs.get(stage, 0.0) + cost
    # modelUsage is the cumulative per-model breakdown for the whole session (its costUSD sums to
    # total_cost_usd); summing across models gives the stage's real token totals. (Top-level .usage
    # is only the final turn, so it under-reports.)
    mu = (captured.get("modelUsage") or {}).values()
    tok = lambda k: sum(m.get(k, 0) for m in mu)
    log.info("Stage %s complete — session=%s cost=$%.4f subtype=%s turns=%s | tokens in=%d out=%d "
             "cache_read=%d cache_write=%d",
             stage, sid[:8], cost, captured.get("subtype"), captured.get("num_turns"),
             tok("inputTokens"), tok("outputTokens"),
             tok("cacheReadInputTokens"), tok("cacheCreationInputTokens"))
    if captured.get("is_error") or captured.get("subtype") != "success":
        log.warning("CC stage %s ended non-success (subtype=%s) — the harness gate on the produced "
                    "artefacts is authoritative regardless", stage, captured.get("subtype"))
    return captured
