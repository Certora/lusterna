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

from . import config, subagents, telemetry
from .state import AgentDeps

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
    """Return the index of the last clean turn start — the last ModelRequest that
    is not purely tool returns. Compacting up to this index never splits a pair."""
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, RetryPromptPart
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, ModelRequest) and not all(
            isinstance(p, (ToolReturnPart, RetryPromptPart)) for p in msg.parts
        ):
            return i
    return 0


@_hooks.on.before_model_request
async def _before_request(
    ctx: RunContext, model_ctx: ModelRequestContext
) -> ModelRequestContext | None:
    if isinstance(ctx.deps, AgentDeps):
        ctx.deps.message_history = list(model_ctx.messages)

    if telemetry.stage.input_tokens >= config.COMPACTION_THRESHOLD:
        telemetry.stage.reset()
        boundary = _turn_boundary(model_ctx.messages)
        if boundary > 0:
            to_compact = list(model_ctx.messages[:boundary])
            to_keep    = list(model_ctx.messages[boundary:])
            log.info(
                "Compaction triggered: compacting %d messages, keeping %d",
                len(to_compact), len(to_keep),
            )
            compacted = await subagents.compact(to_compact)
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
