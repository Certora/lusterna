"""Central configuration drawn from environment variables, plus logging setup."""
import logging
import os
import sys
from pathlib import Path


def setup_logging(verbose: bool = False) -> None:
    """Configure stdlib logging: structured lines on stderr, level from env/-v."""
    level_name = os.environ.get("LUSTERNA_LOG_LEVEL", "DEBUG" if verbose else "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]

    # Third-party HTTP/client chatter (one "HTTP Request: POST …" line per model call, etc.) is
    # noise — the user wants to see what the AGENT is doing, not the transport. Silence to WARNING.
    for noisy in ("httpx", "httpcore", "anthropic", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


MODEL = os.environ.get("LUSTERNA_MODEL", "anthropic:claude-opus-4-8")
JUDGE_MODEL = os.environ.get("LUSTERNA_JUDGE_MODEL", "anthropic:claude-opus-4-8")
# Reasoning effort for effort-capable Anthropic models (Opus 4.x): "", low, medium, high.
# Default "high": agents run with adaptive extended thinking at high effort (see cache_settings).
# Set to "" to disable thinking (e.g. when overriding MODEL to a non-thinking model like Sonnet).
EFFORT = os.environ.get("LUSTERNA_EFFORT", "high").strip().lower()
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


# Raised to 32000 to leave headroom for adaptive extended thinking (which shares the response
# token budget with the output) plus a full Lean proof / spec in one response.
MAX_TOKENS = int(os.environ.get("LUSTERNA_MAX_TOKENS", "32000"))


def cache_settings(model: str) -> dict:
    """Return AnthropicModelSettings with prompt caching, native context management, and raised
    max_tokens. Empty dict for non-Anthropic providers.

    Caching (1h TTL) covers the system prompt, the tool definitions, AND the message prefix
    (`anthropic_cache`), so an agent's growing history is re-sent at the cache-hit rate. The 1h
    TTL survives slow charon/aeneas/lake turns between requests.

    Context management (`clear_tool_uses_20250919`) lets the SERVER drop the oldest tool results
    when the window fills, keeping the recent working set — a cache-friendly, model-native
    replacement for a client-side compaction loop (a client-side rewrite would instead invalidate
    the prompt cache on every trigger and evict context the agent then re-reads).

    max_tokens is raised from pydantic-ai's 4096 default so a full Lean proof fits in one response.
    """
    if not model.startswith("anthropic:"):
        return {}
    try:
        from pydantic_ai.models.anthropic import AnthropicModelSettings
        settings = AnthropicModelSettings(
            anthropic_cache="1h",
            anthropic_cache_instructions="1h",
            anthropic_cache_tool_definitions="1h",
            anthropic_context_management={"edits": [{"type": "clear_tool_uses_20250919"}]},
            max_tokens=MAX_TOKENS,
        )
        # Effort-capable models (Opus 4.x) reason harder with adaptive extended thinking at the
        # configured effort. Sonnet leaves EFFORT empty, so this is a no-op there.
        if EFFORT in ("low", "medium", "high"):
            settings["anthropic_thinking"] = {"type": "adaptive"}
            settings["anthropic_effort"] = EFFORT
        return settings
    except ImportError:
        return {}


# Maximum model requests per stage (pydantic-ai UsageLimits.request_limit).
# 0 or unset means unlimited.
_req_limit_env = os.environ.get("LUSTERNA_REQUEST_LIMIT", "0")
REQUEST_LIMIT: int | None = int(_req_limit_env) if _req_limit_env.strip() not in ("", "0") else None

# Backstop on PROVE model requests (the genuine-progress stop ends PROVE in the normal case;
# this only guards against a runaway). 0/unset = unlimited.
_prove_req = os.environ.get("LUSTERNA_PROVE_REQUEST_LIMIT", "150")
PROVE_REQUEST_LIMIT: int | None = int(_prove_req) if _prove_req.strip() not in ("", "0") else None

# Debug/inspection: stop the pipeline right after TRANSLATE (skip spec/prove/report) so the
# translation artefacts can be examined. Used to iterate on the TRANSLATE stage in isolation.
STOP_AFTER_TRANSLATE = os.environ.get("LUSTERNA_STOP_AFTER_TRANSLATE", "") not in ("", "0")

# Debug/inspection: run through FORMALISE + SPEC-JUDGE but stop just before PROVE, so the
# inferred implementation spec (the judged theorem statements) can be examined without spending
# a prove pass. Used to iterate on the spec stages in isolation.
STOP_BEFORE_PROVE = os.environ.get("LUSTERNA_STOP_BEFORE_PROVE", "") not in ("", "0")
