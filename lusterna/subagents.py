"""Embedded specialists: focused agents called from within pipeline stages.

Unlike pipeline stages (agent.py), these agents receive no message history and
return structured output directly. They are invisible to the pipeline loop.
"""
import logging
from pydantic_ai import Agent

from . import config, telemetry
from .schemas import TheoremEstimate, ProofVerdict  # noqa: F401 — re-exported for callers

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


# ── effort estimator ──────────────────────────────────────────────────────────

_effort_estimator = _agent(
    TheoremEstimate,
    "You are a Lean 4 proof difficulty estimator. Given Lean 4 theorem stubs, classify "
    "each theorem and lemma name into one of four difficulty categories:\n"
    "  trivial — provable in 1-2 tactic steps (rfl, simp, omega, norm_num, decide)\n"
    "  moderate — tractable with real effort (induction, cases, Mathlib lemmas, "
    "multi-step automation)\n"
    "  hard_acceptable — requires advanced techniques beyond typical automation "
    "(deep coinduction, custom Mathlib extensions, novel arguments); sorry is appropriate\n"
    "  likely_misstated — the statement appears logically incorrect (wrong quantifier, "
    "type mismatch, impossible precondition/postcondition)\n"
    "Be conservative: when unsure between moderate and hard_acceptable, prefer "
    "hard_acceptable. Return theorem/lemma names only, not definitions.",
)


async def estimate_theorem_effort(spec_text: str) -> TheoremEstimate:
    log.info("Subagent: estimating theorem proof effort")
    return await _run(
        _effort_estimator,
        f"### Lean 4 formal specification\n{spec_text}\n\n"
        "Classify every theorem and lemma by proof difficulty.",
    )


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
