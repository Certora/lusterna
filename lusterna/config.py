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
            anthropic_cache=True,
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
            max_tokens=MAX_TOKENS,
        )
    except ImportError:
        return {}


# Maximum model requests per stage (pydantic-ai UsageLimits.request_limit).
# 0 or unset means unlimited.
_req_limit_env = os.environ.get("LUSTERNA_REQUEST_LIMIT", "0")
REQUEST_LIMIT: int | None = int(_req_limit_env) if _req_limit_env.strip() not in ("", "0") else None

# PROVE drives the Lean language server via lean-lsp-mcp (baked into the image). Launched
# over `docker exec -i <cid> <bin> --transport stdio --lean-project-path /workspace/out/lean`.
LEAN_LSP_MCP_BIN = os.environ.get("LUSTERNA_LEAN_LSP_MCP_BIN", "/opt/leanmcp/bin/lean-lsp-mcp")
# lean-lsp-mcp tools that need network — disabled under the container's `--network none`.
LEAN_LSP_DISABLED_TOOLS = os.environ.get(
    "LUSTERNA_LEAN_LSP_DISABLED_TOOLS",
    "lean_build,lean_leansearch,lean_loogle,lean_leanfinder,lean_state_search",
)
# FORMALISE is a tool-less structured stage (the whole translation is injected in its prompt
# and the build loop is its convergence gate), so it gets no LSP tools at all — only PROVE
# uses lean-lsp-mcp.
# Backstop on PROVE model requests (the agent self-paces via LSP feedback; this only guards
# against a runaway). 0/unset = unlimited.
_prove_req = os.environ.get("LUSTERNA_PROVE_REQUEST_LIMIT", "150")
PROVE_REQUEST_LIMIT: int | None = int(_prove_req) if _prove_req.strip() not in ("", "0") else None

# Manual compaction: compact when a stage accumulates this many input tokens.
COMPACTION_THRESHOLD = int(os.environ.get("LUSTERNA_COMPACTION_THRESHOLD", "500000"))
COMPACTION_KEEP = int(os.environ.get("LUSTERNA_COMPACTION_KEEP", "4"))
