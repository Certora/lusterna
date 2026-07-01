"""Git helpers — all operations run inside the container via docker exec."""
import logging

from .container import OUT_IN, exec_in

log = logging.getLogger(__name__)


def commit(container_id: str, message: str, glob: str = ".") -> str:
    """Stage *glob* and commit. Returns the new SHA."""
    exec_in(container_id, ["git", "add", glob], workdir=OUT_IN)
    exec_in(container_id, ["git", "commit", "--allow-empty", "-m", message], workdir=OUT_IN)
    _, sha, _ = exec_in(container_id, ["git", "rev-parse", "HEAD"], workdir=OUT_IN)
    sha = sha.strip()
    log.info("Committed %s: %s", sha[:8], message)
    return sha


def head_sha(container_id: str) -> str:
    """Return the current HEAD SHA of the output repo, or empty string if uninitialised."""
    code, out, _ = exec_in(container_id, ["git", "rev-parse", "HEAD"], workdir=OUT_IN)
    return out.strip() if code == 0 else ""


def log_oneline(container_id: str, n: int = 10) -> str:
    _, out, _ = exec_in(container_id, ["git", "log", f"-{n}", "--oneline"], workdir=OUT_IN)
    return out.strip()
