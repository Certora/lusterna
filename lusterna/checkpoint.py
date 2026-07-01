"""Per-session checkpoint directories with incremental checkpoint files.

Layout:
  <SESSIONS_DIR>/
    <session-id>/
      checkpoint-001.json
      checkpoint-002.json
      ...

Each file is a self-contained snapshot written atomically.  The highest-numbered
file is the "latest" checkpoint.  Any prior file can be used to resume an older
state (the caller is responsible for resetting the git output repo accordingly).
"""
import json
import logging
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)


# ── paths ─────────────────────────────────────────────────────────────────────

def session_dir(session_id: str) -> Path:
    d = config.SESSIONS_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path(session_id: str, number: int) -> Path:
    return session_dir(session_id) / f"checkpoint-{number:03d}.json"


def _existing_numbers(session_id: str) -> list[int]:
    d = config.SESSIONS_DIR / session_id
    if not d.exists():
        return []
    return sorted(
        int(p.stem.split("-")[1])
        for p in d.glob("checkpoint-*.json")
    )


def latest_number(session_id: str) -> int | None:
    nums = _existing_numbers(session_id)
    return nums[-1] if nums else None


# ── serialisation helpers ─────────────────────────────────────────────────────

def _serialise_messages(messages: list) -> list:
    if not messages:
        return []
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        return ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    except Exception as exc:
        log.warning("Could not serialise message history: %s", exc)
        return []


def _deserialise_messages(data: list) -> list:
    if not data:
        return []
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        return ModelMessagesTypeAdapter.validate_python(data)
    except Exception as exc:
        log.warning("Could not deserialise message history: %s", exc)
        return []


# ── public API ────────────────────────────────────────────────────────────────

def save(session_id: str, state: dict[str, Any], messages: list | None = None) -> int:
    """Append a new numbered checkpoint and return its number."""
    nums = _existing_numbers(session_id)
    number = (nums[-1] + 1) if nums else 1

    path = _checkpoint_path(session_id, number)
    tmp = path.with_suffix(".tmp")
    payload = {
        "saved_at": time.time(),
        "number": number,
        "state": state,
        "messages": _serialise_messages(messages or []),
    }
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)  # atomic on POSIX
    log.info(
        "Checkpoint saved: %s (number=%d, %d messages)",
        path, number, len(payload["messages"]),
    )
    return number


def load(session_id: str, number: int | None = None) -> dict[str, Any] | None:
    """Load the state dict from a checkpoint.

    *number* selects a specific checkpoint; defaults to the latest.
    Returns None if the session or checkpoint does not exist.
    """
    if number is None:
        number = latest_number(session_id)
    if number is None:
        return None

    path = _checkpoint_path(session_id, number)
    if not path.exists():
        log.warning("Checkpoint %s not found", path)
        return None

    payload = json.loads(path.read_text())
    # Compatibility: bare dicts written by external tools have no "state" key.
    if "state" in payload:
        state = payload["state"]
        saved_at = payload.get("saved_at")
    else:
        state = payload
        saved_at = None
    log.info(
        "Checkpoint loaded: %s (number=%s, saved_at=%s)",
        path, payload.get("number", "?"), saved_at,
    )
    return state


def load_messages(session_id: str, number: int | None = None) -> list:
    """Load and deserialise the message history from a checkpoint."""
    if number is None:
        number = latest_number(session_id)
    if number is None:
        return []

    path = _checkpoint_path(session_id, number)
    if not path.exists():
        return []

    payload = json.loads(path.read_text())
    raw = payload.get("messages", [])
    messages = _deserialise_messages(raw)
    log.info(
        "Loaded %d messages from checkpoint %s", len(messages), path
    )
    return messages


def list_sessions() -> list[str]:
    """Return all session IDs that have at least one checkpoint."""
    if not config.SESSIONS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in config.SESSIONS_DIR.iterdir()
        if d.is_dir() and any(d.glob("checkpoint-*.json"))
    )


def list_checkpoints(session_id: str) -> list[dict]:
    """Return summary info for every checkpoint in a session, oldest first."""
    results = []
    for n in _existing_numbers(session_id):
        path = _checkpoint_path(session_id, n)
        try:
            payload = json.loads(path.read_text())
            state = payload.get("state", payload)
            results.append({
                "number": n,
                "saved_at": payload.get("saved_at"),
                "git_head": state.get("git_head", ""),
                "progress_keys": list(state.get("progress", {}).keys()),
                "messages": len(payload.get("messages", [])),
                "path": str(path),
            })
        except Exception as exc:
            results.append({"number": n, "error": str(exc), "path": str(path)})
    return results
