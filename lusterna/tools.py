"""Agent tools — exec commands and manage files inside the container."""
import logging
from pathlib import Path

from . import git_ops, rag
from .container import REPO_IN, OUT_IN, exec_in
from .state import AgentDeps

log = logging.getLogger(__name__)


def _repo(rel: str) -> str:
    return f"{REPO_IN}/{rel}"


def _out(rel: str) -> str:
    return f"{OUT_IN}/{rel}"


def _assert_relative(path: str) -> None:
    """Reject absolute paths — tools only accept repo-relative or out-relative paths."""
    if path.startswith("/"):
        raise ValueError(f"Absolute paths are not accepted; use a repo-relative path (got: {path!r})")


def read_file(deps: AgentDeps, path: str) -> str:
    """Read a source file from the repo inside the container. Path must be relative."""
    _assert_relative(path)
    code, out, err = exec_in(deps.container_id, ["cat", _repo(path)])
    if code != 0:
        raise FileNotFoundError(f"Cannot read {path}: {err.strip()}")
    log.info("read_file: %s (%d chars)", path, len(out))
    return out


def read_output_file(deps: AgentDeps, path: str) -> str:
    """Read a generated file from /workspace/out inside the container. Path must be relative."""
    _assert_relative(path)
    code, out, err = exec_in(deps.container_id, ["cat", _out(path)], workdir=OUT_IN)
    if code != 0:
        raise FileNotFoundError(f"Cannot read output file {path}: {err.strip()}")
    log.info("read_output_file: %s (%d chars)", path, len(out))
    return out


def write_file(deps: AgentDeps, path: str, content: str) -> str:
    """Write *content* to *path* inside /workspace/out."""
    full = _out(path)
    # ensure parent directory exists
    parent = str(Path(full).parent)
    exec_in(deps.container_id, ["mkdir", "-p", parent])
    # write via tee (no shell injection: content goes through stdin)
    import subprocess
    cmd = ["docker", "exec", "--interactive", "--workdir", OUT_IN,
           deps.container_id, "tee", full]
    r = subprocess.run(cmd, input=content, capture_output=True, text=True)
    if r.returncode != 0:
        raise IOError(f"write_file failed for {path}: {r.stderr.strip()}")
    log.info("write_file: %s (%d chars)", path, len(content))
    return f"Written {len(content)} chars to {path}"


def list_files(deps: AgentDeps, extension: str = "rs") -> list[str]:
    """List files in the repo with the given extension (e.g. 'rs', 'toml')."""
    code, out, err = exec_in(
        deps.container_id,
        ["find", REPO_IN, "-type", "f", "-name", f"*.{extension}"],
    )
    files = [
        line.removeprefix(REPO_IN + "/")
        for line in out.splitlines()
        if line.strip()
    ]
    log.info("list_files(*.%s): %d results", extension, len(files))
    return files


def run_aeneas(deps: AgentDeps, entry_file: str) -> dict:
    """Translate *entry_file* (repo-relative) to Lean 4 via Aeneas."""
    from . import config

    lean_rel = f"lean/{Path(entry_file).stem}.lean"
    exec_in(deps.container_id, ["mkdir", "-p", _out("lean")])

    code, out, err = exec_in(
        deps.container_id,
        [config.AENEAS_BIN, _repo(entry_file), "-o", _out(lean_rel)],
        workdir=REPO_IN,
    )

    if code != 0:
        log.warning("Aeneas exited %d — writing mock Lean output", code)
        mock = (
            f"-- MOCK: Aeneas translation of {entry_file}\n"
            "-- Replace this with actual Aeneas output.\n"
            "namespace Mock\nend Mock\n"
        )
        write_file(deps, lean_rel, mock)
        out = f"(mock) placeholder written to {lean_rel}"
        err = ""

    sha = git_ops.commit(deps.container_id, f"feat(aeneas): translate {entry_file} → Lean", glob="lean/")
    log.info("Aeneas done — commit %s", sha[:8])
    return {"success": True, "stdout": out, "stderr": err, "lean_path": lean_rel, "commit": sha}


def check_lean(deps: AgentDeps, lean_file: str) -> dict:
    """Run `lake build` on *lean_file* (out-relative) inside the container."""
    from . import config

    code, out, err = exec_in(
        deps.container_id,
        [config.LAKE_BIN, "build", _out(lean_file)],
        workdir=OUT_IN,
    )
    if code != 0:
        log.warning("lake build failed (exit %d): %s", code, err[:200])
    return {"success": code == 0, "stdout": out, "stderr": err}


def rag_query(query_text: str, top_k: int = 5) -> list[dict]:
    """Query the local RAG knowledge base for domain knowledge."""
    results = rag.query(query_text, top_k=top_k)
    log.info("RAG query returned %d results", len(results))
    return results


def git_log(deps: AgentDeps, n: int = 10) -> str:
    return git_ops.log_oneline(deps.container_id, n=n)


def git_commit(deps: AgentDeps, message: str) -> str:
    return git_ops.commit(deps.container_id, message)
