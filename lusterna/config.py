"""Central configuration drawn from environment variables."""
import os
from pathlib import Path

MODEL = os.environ.get("LUSTERNA_MODEL", "anthropic:claude-sonnet-4-6")
JUDGE_MODEL = os.environ.get("LUSTERNA_JUDGE_MODEL", "anthropic:claude-sonnet-4-6")
# Root directory that holds per-session checkpoint directories
SESSIONS_DIR = Path(os.environ.get("LUSTERNA_SESSIONS_DIR", "~/.local/share/lusterna/sessions")).expanduser()
CHARON_BIN = os.environ.get("LUSTERNA_CHARON_BIN", "charon")
AENEAS_BIN = os.environ.get("LUSTERNA_AENEAS_BIN", "aeneas")
LAKE_BIN = os.environ.get("LUSTERNA_LAKE_BIN", "lake")
# Docker integration
CONTAINER_IMAGE = os.environ.get("LUSTERNA_IMAGE", "lusterna-toolchain:latest")
# Pre-existing container name/ID to attach to (skips auto-start when set)
CONTAINER_ID = os.environ.get("LUSTERNA_CONTAINER", "")
# Optional cumulative token budget for the entire session (all agents combined).
# 0 or unset means unlimited.
_budget_env = os.environ.get("LUSTERNA_TOKEN_BUDGET", "0")
TOKEN_BUDGET: int | None = int(_budget_env) if _budget_env.strip() not in ("", "0") else None


MAX_TOKENS = int(os.environ.get("LUSTERNA_MAX_TOKENS", "16000"))


def cache_settings(model: str) -> dict:
    """Return AnthropicModelSettings with prompt caching and raised max_tokens.

    Caches system prompt blocks and the last tool-definition block so re-sent
    instructions and tool schemas are billed at the cache-hit rate (~10× cheaper).
    Raises max_tokens from the pydantic-ai default of 4096 so the model can write
    complete Lean proofs without hitting the output limit mid-response.
    Returns an empty dict for non-Anthropic providers.
    """
    if not model.startswith("anthropic:"):
        return {}
    try:
        from pydantic_ai.models.anthropic import AnthropicModelSettings
        return AnthropicModelSettings(
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
            max_tokens=MAX_TOKENS,
        )
    except ImportError:
        return {}


# Manual compaction: compact when a stage accumulates this many input tokens.
COMPACTION_THRESHOLD = int(os.environ.get("LUSTERNA_COMPACTION_THRESHOLD", "500000"))
