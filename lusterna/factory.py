"""Agent factory: stage agent construction with shared hooks.

Cross-cutting concerns (token tracking, budget enforcement, message-history snapshotting)
are wired once here and apply to every stage agent. Context-window management is NOT done
here: it is delegated to the model's native server-side context management (configured in
config.cache_settings), which keeps the prompt cache warm — a client-side compaction loop
would instead rewrite the message prefix on every trigger, invalidating the cache and
evicting context the agent then re-reads.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.models import ModelRequestContext

from . import config, telemetry
from .schemas import AgentDeps

log = logging.getLogger(__name__)

_hooks = Hooks()


@_hooks.on.after_model_request
async def _after_request(ctx: RunContext, *, request_context: ModelRequestContext, response: Any) -> Any:
    usage = getattr(response, "usage", None)
    if usage is not None:
        telemetry.session.record(usage)
        telemetry.stage.record(usage)
        log.debug(
            "request: in=%d out=%d cache_read=%d | stage_in=%d | session=%d/%s",
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            getattr(usage, "cache_read_tokens", 0) or 0,
            telemetry.stage.input_tokens,
            telemetry.session.total(), telemetry.budget or "∞",
        )
    _log_turn(response)
    return response


def _trunc(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


def _bash_action(command: str) -> str:
    """Render a bash call as the agent's chatter + the command. The agent narrates its reasoning
    as leading `#` comment lines before the actual command; lift those out as the chatter (they are
    the revealing 'where its head is at' bit) and show the command after."""
    lines = command.strip().splitlines()
    i, chatter = 0, []
    while i < len(lines) and lines[i].strip().startswith("#"):
        chatter.append(lines[i].strip().lstrip("#").strip())
        i += 1
    rest = " ".join(lines[i:]).strip()          # the command, minus leading reasoning comments
    note = _trunc(" ".join(chatter), 200)
    cmd = "$ " + _trunc(rest, 160) if rest else ""
    return f"{note}  {cmd}".strip() if note else cmd


def _result_action(name: str, args: dict) -> str:
    """Render a structured result tool nicely — the meaningful field, not raw JSON."""
    if "summary" in args:
        return _trunc(str(args["summary"]), 240)
    if "defects" in args:
        d = args["defects"] or []
        return "no defects" if not d else "defects: " + _trunc(
            "; ".join(f"{x.get('kind', x.get('theorem', '?'))}: {x.get('detail', '')}"
                      for x in d if isinstance(x, dict)), 220)
    return f"→ {name}: " + _trunc(json.dumps(args, default=str), 200)


def _action(name: str, args: Any) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    args = args or {}
    if name == "bash":
        return _bash_action(str(args.get("command", "")))
    if name == "setup_lake_project":
        return "setup lake project"
    return _result_action(name, args)


def _log_turn(response: Any) -> None:
    """Per-turn heartbeat — fires once per MODEL REQUEST (≈ once per action), for immediate feedback
    on what the agent is doing and thinking (and whether it is stuck). Shows the turn's prose, the
    reasoning comments it embeds in a bash command, the command itself, and structured results
    rendered by their meaningful field. The bash tool only logs failures, so this is the per-action
    line."""
    bits = []
    for part in getattr(response, "parts", None) or []:
        kind = getattr(part, "part_kind", "")
        if kind in ("text", "thinking"):
            content = (getattr(part, "content", "") or "").strip()
            if content:
                bits.append(_trunc(content, 300))
        elif kind == "tool-call":
            bits.append(_action(getattr(part, "tool_name", "?"), getattr(part, "args", None)))
    line = "  ".join(b for b in bits if b)
    if line:
        log.info("· %s", line)


@_hooks.on.before_model_request
async def _before_request(
    ctx: RunContext, model_ctx: ModelRequestContext
) -> ModelRequestContext | None:
    # Snapshot the live message history (for checkpoint/telemetry) and enforce the session
    # token budget. Context-window management is handled server-side (see module docstring).
    if isinstance(ctx.deps, AgentDeps):
        ctx.deps.message_history = list(model_ctx.messages)

    if telemetry.budget is not None and telemetry.session.total() >= telemetry.budget:
        from pydantic_ai.exceptions import UsageLimitExceeded
        raise UsageLimitExceeded(
            f"Session token budget of {telemetry.budget:,} exhausted "
            f"({telemetry.session.total():,} tokens used)"
        )
    return model_ctx


def make_stage_agent(
    instructions: str,
    *,
    output_type: Any = None,
    retries: int = 0,
    model: str | None = None,
) -> Agent:
    m = model or config.MODEL
    kwargs: dict[str, Any] = dict(
        deps_type=AgentDeps,
        model_settings=config.cache_settings(m),
        capabilities=[_hooks],
        instructions=instructions,
    )
    if output_type is not None:
        kwargs["output_type"] = output_type
    if retries:
        kwargs["retries"] = retries
    return Agent(m, **kwargs)
