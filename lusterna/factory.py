"""Agent factory: stage agent construction with shared hooks.

Cross-cutting concerns (token tracking, compaction, budget enforcement,
message-history snapshotting) are wired once here and apply to every
stage agent in the pipeline.
"""
from __future__ import annotations

import dataclasses
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
    return response


def _turn_boundary(messages: list) -> int:
    """Return the index of the first message to keep after compaction.

    A safe cut point is just before a ModelResponse — to_compact then ends with
    a complete ModelRequest(ToolReturnPart), never splitting a tool-call/return pair.
    Scans backwards from len(messages)-KEEP to find the nearest ModelResponse.
    Returns 0 if there is nothing worth compacting.
    """
    from pydantic_ai.messages import ModelResponse
    if len(messages) <= config.COMPACTION_KEEP:
        return 0
    target = len(messages) - config.COMPACTION_KEEP
    # Snap forward to the nearest ModelResponse at or after target.
    for i in range(target, len(messages)):
        if isinstance(messages[i], ModelResponse):
            return i
    return 0


@_hooks.on.before_model_request
async def _before_request(
    ctx: RunContext, model_ctx: ModelRequestContext
) -> ModelRequestContext | None:
    if isinstance(ctx.deps, AgentDeps):
        ctx.deps.message_history = list(model_ctx.messages)

    if telemetry.stage.input_tokens >= config.COMPACTION_THRESHOLD:
        telemetry.stage.input_tokens -= config.COMPACTION_THRESHOLD
        boundary = _turn_boundary(model_ctx.messages)
        if boundary > 0:
            to_compact = list(model_ctx.messages[:boundary])
            to_keep    = list(model_ctx.messages[boundary:])
            log.info(
                "Compaction triggered: compacting %d messages, keeping %d",
                len(to_compact), len(to_keep),
            )
            compacted = await _compact(to_compact)
            model_ctx = dataclasses.replace(model_ctx, messages=compacted + to_keep)
        else:
            log.debug("Compaction triggered but no compactable messages — resetting counter only")

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


# ── context-compaction summariser ───────────────────────────────────────────────
# The one embedded helper agent, invoked by the compaction hook above to fold a stage's
# older messages into a single summary. It is a PLAIN agent (NO _hooks capability) so it
# never recurses into compaction on its own context.

_summariser = Agent(
    config.MODEL, output_type=str, model_settings=config.cache_settings(config.MODEL),
    instructions=(
        "Summarise the following agent conversation into a concise technical paragraph. "
        "Preserve all file paths, Lean theorem names, lake build errors, git commit SHAs, "
        "and any decisions made. Focus on what was attempted, what succeeded, and what failed."),
)


def _messages_to_text(messages: list) -> str:
    import json
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        return json.dumps(ModelMessagesTypeAdapter.dump_python(messages, mode="json"), indent=2)
    except Exception:
        return str(messages)


async def _compact(messages: list) -> list:
    """Summarise *messages* into a single replacement message. Never fails open: on a summariser
    error the old messages are DROPPED (state lives on disk; the agent re-reads what it needs),
    so a stage's context can never grow without bound."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    try:
        result = await _summariser.run(_messages_to_text(messages))
        telemetry.session.record(result.usage)
        content = f"[COMPACTED CONTEXT — earlier conversation summary]\n{result.output}"
        log.info("Compaction: %d messages → 1 summary message", len(messages))
    except Exception as exc:
        content = ("[COMPACTED CONTEXT — earlier conversation dropped; summariser unavailable. "
                   "Re-read any files you need from disk.]")
        log.warning("Compaction summariser failed (%s) — dropping %d messages", exc, len(messages))
    return [ModelRequest(parts=[UserPromptPart(content=content)])]
