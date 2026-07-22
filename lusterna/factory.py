"""Agent factory: stage agent construction with shared hooks.

Cross-cutting concerns (token tracking, budget enforcement, message-history snapshotting, the
transient-error retry, and server-side context compaction) are wired once here and apply to every
stage agent. Context-window management stays server-side (clear_tool_uses from config.cache_settings
+ the AnthropicCompaction edit added below) to keep the prompt cache warm — a client-side compaction
loop would instead rewrite the message prefix on every trigger, invalidating the cache and evicting
context the agent then re-reads.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities.abstract import AbstractCapability, WrapModelRequestHandler
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.anthropic import AnthropicCompaction

from . import config, telemetry
from .schemas import AgentDeps

log = logging.getLogger(__name__)

_hooks = Hooks()

# ── transient model-error retry ──────────────────────────────────────────────

_TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
_TRANSIENT_MARKERS = ("overloaded", "rate_limit", "timeout", "timed out", "temporarily",
                      "502", "503", "504", "529", "service unavailable")


def _is_transient(exc: ModelAPIError) -> bool:
    """A provider error worth retrying: an overloaded/5xx/rate-limit/timeout blip, not a
    deterministic failure (bad request, auth, unsupported feature)."""
    cause = getattr(exc, "__cause__", None)
    if getattr(cause, "status_code", None) in _TRANSIENT_STATUS:
        return True
    return any(m in str(exc).lower() for m in _TRANSIENT_MARKERS)


class _TransientRetry(AbstractCapability[AgentDeps]):
    """Retry transient model-provider errors with exponential backoff + jitter.

    Retrying the whole (streaming) request is correct: a mid-stream overload discards the partial
    stream. ONLY `ModelAPIError`s judged transient are retried — `UsageLimitExceeded` (budget) and
    every other exception propagate unchanged, so a cap-hit still stops cleanly and a deterministic
    error still surfaces. A transient failure produces little/no output, so a retry mostly re-sends
    cached input."""

    def __init__(self, max_attempts: int, base_delay: float):
        self.max_attempts = max_attempts
        self.base_delay = base_delay

    async def wrap_model_request(
        self, ctx: RunContext[AgentDeps], *,
        request_context: ModelRequestContext, handler: WrapModelRequestHandler,
    ) -> Any:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await handler(request_context)
            except ModelAPIError as exc:
                if attempt >= self.max_attempts or not _is_transient(exc):
                    raise
                delay = min(self.base_delay * 2 ** (attempt - 1), 60.0) + random.uniform(0, 1.0)
                log.warning("Transient model error (attempt %d/%d): %s — retrying in %.1fs",
                            attempt, self.max_attempts, str(exc)[:140], delay)
                await asyncio.sleep(delay)


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
    # AnthropicCompaction appends its `compact_20260112` edit to the clear_tool_uses edit from
    # cache_settings so the two layer (see config.COMPACTION_THRESHOLD for the rationale).
    kwargs: dict[str, Any] = dict(
        deps_type=AgentDeps,
        model_settings=config.cache_settings(m),
        capabilities=[
            _TransientRetry(config.MODEL_RETRY_ATTEMPTS, config.MODEL_RETRY_BASE_DELAY),
            _hooks,
            AnthropicCompaction(token_threshold=config.COMPACTION_THRESHOLD),
        ],
        instructions=instructions,
    )
    if output_type is not None:
        kwargs["output_type"] = output_type
    if retries:
        kwargs["retries"] = retries
    return Agent(m, **kwargs)
