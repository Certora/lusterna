"""Context-compaction summariser — the one embedded helper agent.

Invoked from the compaction hook (factory.py) to fold a stage's older messages into
a single summary. Receives no pipeline message history and is invisible to the loop.
"""
import logging
from pydantic_ai import Agent

from . import config, telemetry

log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _agent(output_type, instructions: str, model: str | None = None) -> Agent:
    m = model or config.MODEL
    return Agent(m, output_type=output_type,
                 model_settings=config.cache_settings(m),
                 instructions=instructions)


async def _run(agent: Agent, prompt: str):
    result = await agent.run(prompt)
    telemetry.session.record(result.usage)
    return result.output


# ── context compaction ────────────────────────────────────────────────────────

_summariser = _agent(
    str,
    "Summarise the following agent conversation into a concise technical paragraph. "
    "Preserve all file paths, Lean theorem names, lake build errors, git commit SHAs, "
    "and any decisions made. Focus on what was attempted, what succeeded, and what failed.",
)


def _messages_to_text(messages: list) -> str:
    import json
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
        return json.dumps(data, indent=2)
    except Exception:
        return str(messages)


async def compact(messages: list) -> list:
    """Summarise *messages* into a single replacement message.

    Never fails open: if the summariser errors (transient 5xx, or a payload that
    exceeds the model context), the old messages are DROPPED rather than kept, so a
    stage's context can never grow without bound. State lives on disk, so the agent
    can recover by re-reading files.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    try:
        summary = await _run(_summariser, _messages_to_text(messages))
        content = f"[COMPACTED CONTEXT — earlier conversation summary]\n{summary}"
        log.info("Compaction: %d messages → 1 summary message", len(messages))
    except Exception as exc:
        content = ("[COMPACTED CONTEXT — earlier conversation dropped; summariser "
                   "unavailable. Re-read any files you need from disk.]")
        log.warning("Compaction summariser failed (%s) — dropping %d messages",
                    exc, len(messages))
    return [ModelRequest(parts=[UserPromptPart(content=content)])]
