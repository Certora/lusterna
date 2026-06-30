"""Filesystem-based session checkpoints stored as JSON files."""
import json
import logging
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)


def _session_path(session_id: str) -> Path:
    config.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    return config.CHECKPOINT_DIR / f"{session_id}.json"


def save(session_id: str, state: dict[str, Any]) -> None:
    path = _session_path(session_id)
    tmp = path.with_suffix(".tmp")
    payload = {"saved_at": time.time(), "state": state}
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)  # atomic on POSIX
    log.info("Checkpoint saved: %s", path)


def load(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    log.info("Checkpoint loaded: %s (saved_at=%s)", path, payload.get("saved_at"))
    return payload["state"]


def list_sessions() -> list[str]:
    if not config.CHECKPOINT_DIR.exists():
        return []
    return [p.stem for p in sorted(config.CHECKPOINT_DIR.glob("*.json"))]
