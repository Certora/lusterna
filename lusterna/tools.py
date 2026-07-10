"""Agent tools — registered directly on pydantic-ai agents.

Public functions take RunContext[AgentDeps] and can be passed to agent.tool() without
any wrapper.  Private helpers (_norm_out, _guard_out, _tee, _write_lakefile) take plain
values and are called internally.  run_aeneas and check_lean are implementation helpers
called from agent.py wrappers that carry additional pipeline logic (progress, checkpoint).
"""
import logging
import subprocess
from pathlib import Path

from pydantic_ai import RunContext

from .container import REPO_IN, OUT_IN, exec_in
from .schemas import AgentDeps

log = logging.getLogger(__name__)

_OUT_PREFIX = f"{OUT_IN}/"
_WRITE_PROTECTED = {"lean-toolchain", "lakefile.lean", "lake-manifest.json", "Cargo.toml", "Cargo.lock"}


def _norm_out(path: str) -> str:
    """Strip the /workspace/out/ prefix so callers may pass either form."""
    if path == OUT_IN:
        return "."
    return path.removeprefix(_OUT_PREFIX)


def _guard_out(path: str) -> str | None:
    """Return an ERROR: string if *path* is write-protected, else None."""
    parts = Path(path).parts
    if ".lake" in parts or ".git" in parts:
        return f"ERROR: writing into .lake/ or .git/ is not allowed (got: {path!r})"
    if Path(path).name in _WRITE_PROTECTED:
        return f"ERROR: {Path(path).name!r} is a protected infrastructure file"
    return None


def _tee(container_id: str, dest: str, content: str, append: bool = False) -> str | None:
    """Write *content* to *dest* via tee; return an error string on failure."""
    cmd = ["docker", "exec", "--interactive", "--workdir", OUT_IN, container_id, "tee"]
    if append:
        cmd.append("--append")
    cmd.append(dest)
    r = subprocess.run(cmd, input=content, capture_output=True, text=True)
    return None if r.returncode == 0 else r.stderr.strip()


def list_files(ctx: RunContext[AgentDeps], extension: str = "rs") -> list[str]:
    """List files with the given extension ('rs', 'lean', 'toml', …).
    Lean files are searched in /workspace/out; all others in the Rust repo."""
    search_root = OUT_IN if extension == "lean" else REPO_IN
    _, out, _ = exec_in(ctx.deps.container_id,
                        ["find", search_root, "-type", "f", "-name", f"*.{extension}"])
    files = [line.removeprefix(search_root + "/") for line in out.splitlines() if line.strip()]
    log.info("list_files(*.%s): %d results", extension, len(files))
    return files


def read_out(deps: AgentDeps, path: str) -> str:
    """Read a generated file from /workspace/out. For direct orchestration calls."""
    path = _norm_out(path)
    code, out, err = exec_in(deps.container_id, ["cat", f"{OUT_IN}/{path}"], workdir=OUT_IN)
    if code != 0:
        return f"ERROR: cannot read '{path}': {err.strip()}"
    log.info("read_out: %s (%d chars)", path, len(out))
    return out


def read_repo_sources(deps: AgentDeps) -> str:
    """Read all .rs files plus Cargo.toml and build.rs from the repo.

    Returns a single formatted block suitable for injection into a stage prompt.
    Files under target/ are excluded. Used by the orchestrator to pre-load sources
    for EXPLORE without requiring the agent to call list_files / read_file.
    """
    _, out, _ = exec_in(deps.container_id, [
        "find", REPO_IN, "-type", "f",
        "(", "-name", "*.rs", "-o", "-name", "Cargo.toml", "-o", "-name", "build.rs", ")",
        "!", "-path", "*/target/*",
    ])
    paths = sorted(line for line in out.splitlines() if line.strip())
    parts = []
    for abs_path in paths:
        rel = abs_path.removeprefix(REPO_IN + "/")
        code, content, _ = exec_in(deps.container_id, ["cat", abs_path])
        if code == 0:
            parts.append(f"### {rel}\n{content}")
    log.info("read_repo_sources: %d files", len(parts))
    return "\n\n".join(parts)


def read_output_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a generated file from /workspace/out (relative or absolute path).
    Use list_files('lean') to discover available files. Returns ERROR: on failure."""
    return read_out(ctx.deps, path)


def prune_stray_specs(deps: AgentDeps, translation: str) -> None:
    """Remove stray files the FORMALISE agent may create outside the canonical layout:
    the retired specs/formal_spec.lean and any top-level lean/*Spec.lean orphan.

    The real spec is nested at lean/<Crate>/Spec.lean (depth 2), so -maxdepth 1 spares
    it; *translation* (lean/<Crate>.lean) is excluded by name in case a crate is itself
    named *Spec.
    """
    keep = Path(translation).name if translation else ""
    cmd = (
        f"rm -f {OUT_IN}/specs/formal_spec.lean; "
        f"find {OUT_IN}/lean -maxdepth 1 -name '*Spec.lean'"
        + (f" ! -name '{keep}'" if keep else "")
        + " -delete"
    )
    exec_in(deps.container_id, ["sh", "-c", cmd])
    log.info("Pruned stray spec files")


def write_out(deps: AgentDeps, path: str, content: str) -> str:
    """Write *content* to *path* in /workspace/out. For direct orchestration calls.

    Unlike write_file, this does not guard against empty content — the caller is
    responsible. Returns an ERROR: string on failure.
    """
    path = _norm_out(path)
    if err := _guard_out(path):
        return err
    full = f"{OUT_IN}/{path}"
    exec_in(deps.container_id, ["mkdir", "-p", str(Path(full).parent)])
    if fail := _tee(deps.container_id, full, content):
        return f"ERROR: write_out failed for '{path}': {fail}"
    log.info("write_out: %s (%d chars)", path, len(content))
    return f"Written {len(content)} chars to {path}"


def write_file(ctx: RunContext[AgentDeps], path: str, content: str = "") -> str:
    """Write *content* to *path* in /workspace/out (relative or absolute).
    Returns ERROR: if content is empty, the path is protected, or the write fails."""
    if not content:
        return "ERROR: content is required — retry write_file with the full file content"
    return write_out(ctx.deps, path, content)


def search_output_file(ctx: RunContext[AgentDeps], path: str, pattern: str) -> str:
    """Grep *pattern* in an output file; returns '<lineno>:<line>' per match (or '(no matches)').
    *path* may be relative or absolute. Use to locate a theorem before calling read_output_lines."""
    path = _norm_out(path)
    code, out, err = exec_in(ctx.deps.container_id, ["grep", "-n", pattern, f"{OUT_IN}/{path}"])
    if code == 1:
        return "(no matches)"
    if code != 0:
        return f"ERROR: grep failed for '{path}': {err.strip()}"
    log.info("search_output_file: %s %r → %d lines", path, pattern, out.count("\n"))
    return out


def read_output_lines(ctx: RunContext[AgentDeps], path: str, start: int, end: int) -> str:
    """Read lines *start*–*end* (1-indexed, inclusive) from an output file as '<lineno>: <content>'.
    *path* may be relative or absolute. Use search_output_file first to find the right line numbers."""
    path = _norm_out(path)
    code, out, err = exec_in(ctx.deps.container_id,
                             ["sed", "-n", f"{start},{end}p", f"{OUT_IN}/{path}"])
    if code != 0:
        return f"ERROR: read_output_lines failed for '{path}': {err.strip()}"
    lines = out.splitlines()
    log.info("read_output_lines: %s [%d-%d] → %d lines", path, start, end, len(lines))
    return "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines))


def patch_output_lines(ctx: RunContext[AgentDeps], path: str, start: int, end: int, content: str) -> str:
    """Replace lines *start*–*end* (1-indexed, inclusive) with *content*; all other lines are preserved.
    *path* may be relative or absolute. Use to update a single theorem proof. Returns ERROR: on failure."""
    path = _norm_out(path)
    if err := _guard_out(path):
        return err
    full = f"{OUT_IN}/{path}"
    code, raw, err = exec_in(ctx.deps.container_id, ["cat", full])
    if code != 0:
        return f"ERROR: cannot read '{path}' for patching: {err.strip()}"
    lines = raw.splitlines(keepends=True)
    if start < 1 or end > len(lines) or start > end:
        return f"ERROR: range [{start},{end}] out of bounds for '{path}' ({len(lines)} lines)"
    replacement = content if content.endswith("\n") else content + "\n"
    new_content = "".join(lines[:start - 1] + [replacement] + lines[end:])
    if fail := _tee(ctx.deps.container_id, full, new_content):
        return f"ERROR: patch_output_lines failed for '{path}': {fail}"
    log.info("patch_output_lines: %s [%d-%d]", path, start, end)
    return f"Patched lines {start}–{end} of {path}"


# ── git (all operations run inside the container via docker exec) ────────────────

def commit(container_id: str, message: str, glob: str = ".") -> str:
    """Stage *glob* and commit in /workspace/out. Returns the new SHA."""
    exec_in(container_id, ["git", "add", glob], workdir=OUT_IN)
    exec_in(container_id, ["git", "commit", "--allow-empty", "-m", message], workdir=OUT_IN)
    _, sha, _ = exec_in(container_id, ["git", "rev-parse", "HEAD"], workdir=OUT_IN)
    sha = sha.strip()
    log.info("Committed %s: %s", sha[:8], message)
    return sha


def head_sha(container_id: str) -> str:
    """Current HEAD SHA of the output repo, or empty string if uninitialised."""
    code, out, _ = exec_in(container_id, ["git", "rev-parse", "HEAD"], workdir=OUT_IN)
    return out.strip() if code == 0 else ""


def log_oneline(container_id: str, n: int = 10) -> str:
    _, out, _ = exec_in(container_id, ["git", "log", f"-{n}", "--oneline"], workdir=OUT_IN)
    return out.strip()


def git_log(ctx: RunContext[AgentDeps], n: int = 10) -> str:
    """Show the last *n* commits in /workspace/out."""
    return log_oneline(ctx.deps.container_id, n=n)


def git_commit(ctx: RunContext[AgentDeps], message: str) -> str:
    """Stage all changes in /workspace/out and create a git commit."""
    return commit(ctx.deps.container_id, message)
