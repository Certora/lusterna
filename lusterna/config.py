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


# Reasoning effort for the Claude Code stage sessions (`--effort`): "", low, medium, high, xhigh,
# max. "" disables extended thinking. Default "high".
EFFORT = os.environ.get("LUSTERNA_EFFORT", "high").strip().lower()
# Root directory that holds per-session checkpoint directories
SESSIONS_DIR = Path(os.environ.get("LUSTERNA_SESSIONS_DIR", "~/.local/share/lusterna/sessions")).expanduser()
# Docker integration
CONTAINER_IMAGE = os.environ.get("LUSTERNA_IMAGE", "lusterna-toolchain:latest")
# Pre-existing container name/ID to attach to (skips auto-start when set)
CONTAINER_ID = os.environ.get("LUSTERNA_CONTAINER", "")

# The ONE knob bounding every stage's gate loop: a stage gives up after this many consecutive
# rounds that fail to pass its trusted gate (for PROVE, this many rounds with no gain in the
# axiom-clean established-theorem count). No hard round ceiling beyond it; each round is also
# cost-capped by Claude Code's --max-budget-usd.
STALL_ROUNDS = int(os.environ.get("LUSTERNA_STALL_ROUNDS", "3"))

# ── Claude Code engine (spawn-model stages) ──────────────
# The CLI `--model` alias for the Claude Code sessions we spawn per stage (e.g. "opus", "sonnet").
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

# Gate FORMALISE on SPEC SCHEMA CONFORMANCE: every theorem must declare `@[lusterna_invariant]`,
# `@[lusterna_hoare]` or `@[lusterna_freeform "why"]` and actually conform to it (lean.check_schemas).
#
# DEFAULT OFF, deliberately. This is the only mechanical spec check the harness may itself gate on —
# a theorem failing the schema its own author declared is a fact, not a judgment — but turning it on
# makes every un-annotated spec a hard stop, so existing campaigns must be migrated first. Flip the
# default only once the schemas have been validated on a real campaign.
SCHEMA_GATE = os.environ.get("LUSTERNA_SCHEMA_GATE", "") not in ("", "0")
