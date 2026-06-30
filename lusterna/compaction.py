"""Context compaction: summarise the agent message history when it grows too large.

Token counting uses a simple character-based heuristic (4 chars ≈ 1 token)
so we avoid an extra API call just to count tokens.  The threshold is
intentionally conservative — we summarise before the model starts losing
coherence, not after.
"""
import logging
from typing import Any

from pydantic_ai.messages import ModelMessage

from . import config

log = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4


def _estimate_tokens(messages: list[ModelMessage]) -> int:
    total = 0
    for msg in messages:
        for part in getattr(msg, "parts", []):
            text = getattr(part, "content", "") or ""
            if isinstance(text, str):
                total += len(text) // _CHARS_PER_TOKEN
    return total


def needs_compaction(messages: list[ModelMessage]) -> bool:
    est = _estimate_tokens(messages)
    log.debug("Estimated context tokens: %d (threshold=%d)", est, config.COMPACTION_THRESHOLD)
    return est >= config.COMPACTION_THRESHOLD


async def compact(
    messages: list[ModelMessage],
    summarise_fn,  # async (text: str) -> str
) -> list[ModelMessage]:
    """Summarise all but the last two messages and return a shortened list.

    *summarise_fn* is expected to call the model and return a plain-text summary.
    The last two messages are kept verbatim so the model retains immediate context.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, TextPart

    if len(messages) <= 2:
        return messages

    keep = messages[-2:]
    to_summarise = messages[:-2]

    # Serialise messages to plain text for the summariser
    lines: list[str] = []
    for msg in to_summarise:
        role = "assistant" if isinstance(msg, ModelResponse) else "user"
        for part in getattr(msg, "parts", []):
            text = getattr(part, "content", "") or getattr(part, "text", "")
            if isinstance(text, str) and text.strip():
                lines.append(f"[{role}] {text.strip()}")

    bulk = "\n".join(lines)
    log.info("Compacting context: summarising %d messages (%d chars)", len(to_summarise), len(bulk))
    summary = await summarise_fn(bulk)

    summary_msg = ModelRequest(
        parts=[SystemPromptPart(content=f"[CONTEXT SUMMARY]\n{summary}")]
    )
    log.info("Compaction done — summary length: %d chars", len(summary))
    return [summary_msg] + list(keep)
