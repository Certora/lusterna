"""Shared dependency type injected into every agent tool via RunContext."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentDeps:
    container_id: str
    repo_path: Path        # host path — used only for initial docker cp push
    work_path: Path        # host path — used only for final docker cp pull
    session_id: str
    design_doc: str
    progress: dict[str, Any] = field(default_factory=dict)
    message_history: list = field(default_factory=list)
