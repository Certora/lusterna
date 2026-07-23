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
# Docker integration
CONTAINER_IMAGE = os.environ.get("LUSTERNA_IMAGE", "lusterna-toolchain:latest")
# Pre-existing container name/ID to attach to (skips auto-start when set)
CONTAINER_ID = os.environ.get("LUSTERNA_CONTAINER", "")
# Optional cumulative token budget for the entire session (all agents combined).
# 0 or unset means unlimited.
_budget_env = os.environ.get("LUSTERNA_TOKEN_BUDGET", "0")
TOKEN_BUDGET: int | None = int(_budget_env) if _budget_env.strip() not in ("", "0") else None


# Claude Code owns model retry (transient blips) and context compaction natively, and caps cost
# per stage via --max-budget-usd, so the old pydantic-ai model settings, client-side retry, and
# compaction-threshold knobs are gone (see DESIGN-claude-code-discipline.md §8).

# The ONE knob governing all three iterative agent loops (TRANSLATE, FORMALISE, PROVE): a loop gives
# up after this many consecutive rounds that fail to beat the best progress seen (in FORMALISE, a
# theorem is quarantined after this many failed-to-compile rounds; in PROVE, best = the axiom-clean
# established-theorem count). There is deliberately NO hard round ceiling — the token budget is the
# precise resource guard, and this progress-based stall (best-tracked, so it catches plateaus and
# oscillation, not just an identical repeat) is the smart backstop that stops a non-converging loop.
STALL_ROUNDS = int(os.environ.get("LUSTERNA_STALL_ROUNDS", "3"))

# ── Claude Code engine (spawn-model stages; see DESIGN-claude-code-discipline.md) ──────────────
# The CLI `--model` alias for the Claude Code sessions we spawn per stage (e.g. "opus", "sonnet").
# This is the Claude-Code alias form, distinct from MODEL's "anthropic:…" pydantic-ai form.
CC_MODEL = os.environ.get("LUSTERNA_CC_MODEL", "opus")
# Per-stage hard dollar cap (`claude --max-budget-usd`) — a runaway backstop, NOT a work limiter.
# Set generously; it should never bind on a healthy stage.
CC_STAGE_BUDGET_USD = float(os.environ.get("LUSTERNA_CC_STAGE_BUDGET_USD", "50"))

# Debug/inspection: stop right after EXPLORE so its assessment artefacts can be examined in
# isolation (used while migrating stages to the Claude-Code spawn model).
STOP_AFTER_EXPLORE = os.environ.get("LUSTERNA_STOP_AFTER_EXPLORE", "") not in ("", "0")

# Debug/inspection: stop the pipeline right after TRANSLATE (skip spec/prove/report) so the
# translation artefacts can be examined. Used to iterate on the TRANSLATE stage in isolation.
STOP_AFTER_TRANSLATE = os.environ.get("LUSTERNA_STOP_AFTER_TRANSLATE", "") not in ("", "0")

# Debug/inspection: run through FORMALISE + SPEC-JUDGE but stop just before PROVE, so the
# inferred implementation spec (the judged theorem statements) can be examined without spending
# a prove pass. Used to iterate on the spec stages in isolation.
STOP_BEFORE_PROVE = os.environ.get("LUSTERNA_STOP_BEFORE_PROVE", "") not in ("", "0")
