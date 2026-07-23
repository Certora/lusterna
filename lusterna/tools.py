"""Harness IO helpers: the orchestrator's own file IO and git inside the container — reading
generated artefacts, writing scaffolding, committing, and the source-diff accountability trail.

Under the spawn model each stage agent is a Claude Code session with its OWN tools (Bash/Read/
Write/Edit), so nothing here is exposed to an agent; these are plain functions the pipeline spine
(pipeline.py) calls directly to inject inputs, run gates, and checkpoint."""
import logging
import subprocess
from pathlib import Path

from .container import REPO_IN, OUT_IN, exec_in
from .schemas import AgentDeps

log = logging.getLogger(__name__)

_OUT_PREFIX = f"{OUT_IN}/"


def _norm_out(path: str) -> str:
    """Strip the /workspace/out/ prefix so callers may pass either form."""
    if path == OUT_IN:
        return "."
    return path.removeprefix(_OUT_PREFIX)


def _tee(container_id: str, dest: str, content: str, append: bool = False) -> str | None:
    """Write *content* to *dest* via tee; return an error string on failure."""
    cmd = ["docker", "exec", "--interactive", "--workdir", OUT_IN, container_id, "tee"]
    if append:
        cmd.append("--append")
    cmd.append(dest)
    r = subprocess.run(cmd, input=content, capture_output=True, text=True)
    return None if r.returncode == 0 else r.stderr.strip()


# ── harness file IO (plain values; not agent tools) ──────────────────────────

def read_out(deps: AgentDeps, path: str) -> str:
    """Read a generated file from /workspace/out. Returns ERROR: on failure."""
    path = _norm_out(path)
    code, out, err = exec_in(deps.container_id, ["cat", f"{OUT_IN}/{path}"], workdir=OUT_IN)
    if code != 0:
        return f"ERROR: cannot read '{path}': {err.strip()}"
    return out


def prune_stray_specs(deps: AgentDeps, translation: str) -> None:
    """Remove stray files a stage may create outside the canonical layout: the retired
    specs/formal_spec.lean and any top-level lean/*Spec.lean orphan. The real spec is nested
    at lean/<Crate>/Spec.lean (depth 2), so -maxdepth 1 spares it; *translation*
    (lean/<Crate>.lean) is excluded by name in case a crate is itself named *Spec."""
    keep = Path(translation).name if translation else ""
    cmd = (
        f"rm -f {OUT_IN}/specs/formal_spec.lean; "
        f"find {OUT_IN}/lean -maxdepth 1 -name '*Spec.lean'"
        + (f" ! -name '{keep}'" if keep else "")
        + " -delete"
    )
    exec_in(deps.container_id, ["sh", "-c", cmd])
    log.info("Pruned stray spec files")


def _write(container_id: str, full: str, content: str, label: str, display: str) -> str:
    """mkdir -p the parent and write *content* to the absolute path *full* via _tee.
    Returns the success message or an ERROR: string."""
    exec_in(container_id, ["mkdir", "-p", str(Path(full).parent)])
    if fail := _tee(container_id, full, content):
        return f"ERROR: {label} failed for '{display}': {fail}"
    log.info("%s: %s (%d chars)", label, display, len(content))
    return f"Written {len(content)} chars to {display}"


def write_out(deps: AgentDeps, path: str, content: str) -> str:
    """Write *content* to *path* in /workspace/out (harness artefact assembly). Returns
    an ERROR: string on failure."""
    path = _norm_out(path)
    return _write(deps.container_id, f"{OUT_IN}/{path}", content, "write_out", path)


# ── source-repo git (edits are made by the agent via bash; the harness reads the diff
#    from REPO_IN's git for the accountability trail) ──────────────────────────

def _repo_baseline(container_id: str) -> str:
    """SHA of the pristine baseline — the root commit init_repo_git made before any edits."""
    _, out, _ = exec_in(container_id, ["git", "rev-list", "--max-parents=0", "HEAD"],
                        workdir=REPO_IN)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return lines[-1] if lines else "HEAD"


def repo_diff(container_id: str) -> str:
    """Combined diff of the Rust source since the pristine baseline (working tree vs the root
    commit, so it captures edits whether or not the agent committed them). Empty if the agent
    made no source edits. The authoritative record of every source modification, for human
    review and the TRANSLATE accountability trail."""
    _, out, _ = exec_in(container_id, ["git", "diff", _repo_baseline(container_id),
                                       "--stat", "--patch"], workdir=REPO_IN)
    return out


def repo_changed_files(container_id: str) -> list[str]:
    """Repo-relative paths of Rust-source files changed since the pristine baseline."""
    _, out, _ = exec_in(container_id, ["git", "diff", _repo_baseline(container_id),
                                       "--name-only"], workdir=REPO_IN)
    return sorted({l.strip() for l in out.splitlines() if l.strip()})


# ── output-repo git (harness commits + checkpoint head) ──────────────────────

def commit(container_id: str, message: str, glob: str = ".", workdir: str = OUT_IN) -> str:
    """Stage *glob* and commit in *workdir* (default /workspace/out). Returns the new SHA."""
    exec_in(container_id, ["git", "add", glob], workdir=workdir)
    exec_in(container_id, ["git", "commit", "--allow-empty", "-m", message], workdir=workdir)
    _, sha, _ = exec_in(container_id, ["git", "rev-parse", "HEAD"], workdir=workdir)
    sha = sha.strip()
    log.info("Committed %s: %s", sha[:8], message)
    return sha


def head_sha(container_id: str) -> str:
    """Current HEAD SHA of the output repo, or empty string if uninitialised."""
    code, out, _ = exec_in(container_id, ["git", "rev-parse", "HEAD"], workdir=OUT_IN)
    return out.strip() if code == 0 else ""
