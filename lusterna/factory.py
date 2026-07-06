"""Agent factory: centralises Agent construction, caching, and session-wide usage tracking.

All agents — pipeline stages and embedded specialists — are created here so that
cross-cutting concerns (prompt caching, token budget enforcement, message-history
snapshotting) are wired once rather than at each construction site.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.models import ModelRequestContext

from . import config
from .state import AgentDeps

log = logging.getLogger(__name__)


# ── session-wide usage accumulator ────────────────────────────────────────────

_input_tokens:       int = 0
_output_tokens:      int = 0
_cache_read_tokens:  int = 0
_cache_write_tokens: int = 0
_requests:           int = 0
_budget:             int | None = None  # None = unlimited


def set_budget(tokens: int | None) -> None:
    global _budget
    _budget = tokens


def record_usage(usage: Any) -> None:
    """Accumulate token counts from a pydantic-ai RunUsage object."""
    global _input_tokens, _output_tokens, _cache_read_tokens, _cache_write_tokens, _requests
    _input_tokens       += getattr(usage, "input_tokens", 0) or 0
    _output_tokens      += getattr(usage, "output_tokens", 0) or 0
    _cache_read_tokens  += getattr(usage, "cache_read_tokens", 0) or 0
    _cache_write_tokens += getattr(usage, "cache_write_tokens", 0) or 0
    _requests           += getattr(usage, "requests", 1) or 1
    total = _input_tokens + _output_tokens
    log.debug(
        "request usage: in=%d out=%d cache_read=%d cache_write=%d | session total=%d/%s",
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
        getattr(usage, "cache_read_tokens", 0) or 0,
        getattr(usage, "cache_write_tokens", 0) or 0,
        total, _budget or "∞",
    )


def get_usage() -> dict:
    """Return a snapshot of session-wide token usage."""
    return {
        "input_tokens":       _input_tokens,
        "output_tokens":      _output_tokens,
        "cache_read_tokens":  _cache_read_tokens,
        "cache_write_tokens": _cache_write_tokens,
        "total_tokens":       _input_tokens + _output_tokens,
        "requests":           _requests,
        "budget":             _budget,
    }


def reset_usage() -> None:
    global _input_tokens, _output_tokens, _cache_read_tokens, _cache_write_tokens, _requests
    _input_tokens = _output_tokens = _cache_read_tokens = _cache_write_tokens = _requests = 0


def _total_tokens() -> int:
    return _input_tokens + _output_tokens


# ── manual compaction ─────────────────────────────────────────────────────────
# When a stage accumulates more than COMPACTION_THRESHOLD messages, summarise
# the older half via a lightweight subagent and replace those messages with a
# single summary message — keeping the most recent KEEP_RECENT messages verbatim.
#
# The summariser is intentionally NOT wired into _hooks to prevent re-entrancy.

_compacting = False   # re-entrancy guard


def _messages_to_text(messages: list) -> str:
    """Serialise pydantic-ai message objects to a readable text block."""
    import json
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
        return json.dumps(data, indent=2)
    except Exception:
        return str(messages)


async def _compact(messages: list) -> list:
    """Summarise *messages* and return a replacement single-message list."""
    global _compacting
    _compacting = True
    try:
        text = _messages_to_text(messages)
        summariser = Agent(
            config.MODEL,
            output_type=str,
            model_settings=config.cache_settings(config.MODEL),
            instructions=(
                "Summarise the following agent conversation into a concise technical paragraph. "
                "Preserve all file paths, Lean theorem names, lake build errors, git commit SHAs, "
                "and any decisions made. Focus on what was attempted, what succeeded, and what failed."
            ),
        )
        result = await summariser.run(text)
        record_usage(result.usage)
        summary = result.output
    except Exception as exc:
        log.warning("Compaction summariser failed (%s) — keeping messages as-is", exc)
        return messages
    finally:
        _compacting = False

    from pydantic_ai.messages import ModelRequest, UserPromptPart
    summary_msg = ModelRequest(parts=[UserPromptPart(
        content=f"[COMPACTED CONTEXT — earlier conversation summary]\n{summary}"
    )])
    log.info("Compaction: %d messages → 1 summary message", len(messages))
    return [summary_msg]


# ── shared hooks ──────────────────────────────────────────────────────────────
# One Hooks instance wired into every agent so the callbacks fire for all
# model requests: stage agents and subagent specialists.

_hooks = Hooks()


@_hooks.on.before_model_request
async def _before_request(
    ctx: RunContext, model_ctx: ModelRequestContext
) -> ModelRequestContext | None:
    # Snapshot message history into deps so mid-stage _checkpoint() calls see
    # up-to-date history. Subagents have no AgentDeps, so the check is safe.
    if isinstance(ctx.deps, AgentDeps):
        ctx.deps.message_history = list(model_ctx.messages)

    log.debug("model request: messages=%d", len(model_ctx.messages))

    # Manual compaction: when messages exceed the threshold, summarise the older
    # portion and replace it with a single summary message.
    n = len(model_ctx.messages)
    if not _compacting and n >= config.COMPACTION_THRESHOLD:
        keep = config.COMPACTION_KEEP_RECENT
        to_compact = list(model_ctx.messages[:-keep])
        to_keep    = list(model_ctx.messages[-keep:])
        log.info(
            "Compaction triggered: %d messages >= threshold %d — compacting %d, keeping %d",
            n, config.COMPACTION_THRESHOLD, len(to_compact), len(to_keep),
        )
        compacted = await _compact(to_compact)
        import dataclasses
        model_ctx = dataclasses.replace(model_ctx, messages=compacted + to_keep)

    # Budget enforcement: raise before we spend more tokens than allowed.
    if _budget is not None and _total_tokens() >= _budget:
        from pydantic_ai.exceptions import UsageLimitExceeded
        raise UsageLimitExceeded(
            f"Session token budget of {_budget:,} exhausted "
            f"({_total_tokens():,} tokens used)"
        )
    return model_ctx


# ── factory functions ─────────────────────────────────────────────────────────

def make_stage_agent(
    instructions: str,
    *,
    output_type: Any = None,
    retries: int = 0,
    model: str | None = None,
) -> Agent:
    """Create a pipeline stage agent with all cross-cutting concerns pre-wired.

    Stage agents use AgentDeps as their deps type and share the session message
    history via the snapshot hook.
    """
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


def make_subagent(
    output_type: Any,
    instructions: str,
    *,
    model: str | None = None,
) -> Agent:
    """Create an embedded specialist agent with caching and budget tracking pre-wired.

    Subagents have no deps (they receive no AgentDeps) and return structured
    Pydantic output directly to the calling stage via a tool call.
    """
    m = model or config.MODEL
    return Agent(
        m,
        output_type=output_type,
        model_settings=config.cache_settings(m),
        capabilities=[_hooks],
        instructions=instructions,
    )
