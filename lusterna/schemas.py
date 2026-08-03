"""The shared dependency object threaded through the pipeline.

Under the spawn model the stages are Claude Code sessions that communicate via FILES under
/workspace/out (read/gated by the harness), so there are no structured-output schemas — the
per-stage deliverable shapes are documented in briefings.py and validated by the trusted gates."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentDeps:
    container_id: str
    repo_path: Path        # host target repo — source is pushed in from here, the run branch lands back here
    session_id: str
    design_doc: str
    progress: dict[str, Any] = field(default_factory=dict)
    # Set True only when the pipeline runs fully to completion (through REPORT). Drives container
    # lifecycle: an incomplete run (budget hit, interrupt, crash) keeps its container ALIVE so a
    # resume can re-attach with full state (source edits, verification/, the run branch).
    completed: bool = False
