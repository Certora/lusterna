"""Agent factory: stage agent construction with shared hooks.

Cross-cutting concerns (token tracking, budget enforcement, message-history snapshotting)
are wired once here and apply to every stage agent. Context-window management is NOT done
here: it is delegated to the model's native server-side context management (configured in
config.cache_settings), which keeps the prompt cache warm — a client-side compaction loop
would instead rewrite the message prefix on every trigger, invalidating the cache and
evicting context the agent then re-reads.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.anthropic import AnthropicCompaction

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
    # AnthropicCompaction APPENDS a `compact_20260112` edit to the context_management edits in
    # cache_settings (which already carries clear_tool_uses), so the two layer rather than clash:
    # tool results are cleared cheaply per turn, and the server compacts older messages — including
    # the agent's own large notes, which clear_tool_uses cannot evict — once input tokens cross the
    # threshold. This guards long campaigns from context-window overflow; the agent's journaled
    # files survive compaction, so the summarised turns are recoverable by re-reading them.
    kwargs: dict[str, Any] = dict(
        deps_type=AgentDeps,
        model_settings=config.cache_settings(m),
        capabilities=[_hooks, AnthropicCompaction(token_threshold=config.COMPACTION_THRESHOLD)],
        instructions=instructions,
    )
    if output_type is not None:
        kwargs["output_type"] = output_type
    if retries:
        kwargs["retries"] = retries
    return Agent(m, **kwargs)
