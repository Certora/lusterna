"""Central configuration drawn from environment variables."""
import os
from pathlib import Path

MODEL = os.environ.get("LUSTERNA_MODEL", "anthropic:claude-sonnet-4-6")
JUDGE_MODEL = os.environ.get("LUSTERNA_JUDGE_MODEL", "anthropic:claude-sonnet-4-6")
# Token budget before triggering context compaction
COMPACTION_THRESHOLD = int(os.environ.get("LUSTERNA_COMPACTION_THRESHOLD", "80000"))
# Directory that holds the RAG knowledge base (JSONL + .npy embeddings)
RAG_DB_PATH = Path(os.environ.get("LUSTERNA_RAG_DB", "~/.local/share/lusterna/rag")).expanduser()
# Root directory that holds per-session checkpoint directories
SESSIONS_DIR = Path(os.environ.get("LUSTERNA_SESSIONS_DIR", "~/.local/share/lusterna/sessions")).expanduser()
CHARON_BIN = os.environ.get("LUSTERNA_CHARON_BIN", "charon")
AENEAS_BIN = os.environ.get("LUSTERNA_AENEAS_BIN", "aeneas")
LAKE_BIN = os.environ.get("LUSTERNA_LAKE_BIN", "lake")
# Docker integration
CONTAINER_IMAGE = os.environ.get("LUSTERNA_IMAGE", "lusterna-toolchain:latest")
# Pre-existing container name/ID to attach to (skips auto-start when set)
CONTAINER_ID = os.environ.get("LUSTERNA_CONTAINER", "")
