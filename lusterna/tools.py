"""Agent tools + harness IO helpers.

The agents drive the container through ONE tool — `bash` — plus `setup_lake_project`
(build-environment provisioning that is not a plain shell one-liner). Everything a stage
used to do through a dozen bespoke tools (list/read/search/patch/write files, git, build)
is now just a shell command.

The remaining functions take plain values (not RunContext) and are HARNESS helpers: the
orchestrator's own file IO and git, used to inject inputs, assemble artefacts, run gates,
and checkpoint. They are not exposed to agents.
"""
import logging
import subprocess
from pathlib import Path

from pydantic_ai import RunContext

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


# ── the agent's tools ────────────────────────────────────────────────────────

_BASH_MAX = 30000  # chars of combined output to return (errors are usually at the end)


def bash(ctx: RunContext[AgentDeps], command: str, workdir: str = "/workspace",
         timeout: int = 900) -> str:
    """Run *command* in the container via `bash -lc` and return `exit=<code>` + its output.

    This is your primary tool. Use it to run the toolchain (charon, aeneas, cargo, lake) and
    ordinary shell utilities: cat / grep / sed / find / git, and `cat > file <<'EOF' … EOF`
    (or tee) to write or edit files. The Rust repo is at /workspace/repo and generated Lean
    goes under /workspace/out/lean. workdir defaults to /workspace; either pass workdir or
    `cd` inside the command. charon, aeneas, cargo, lake are all on PATH. Combined
    stdout+stderr is truncated to the last ~30k chars."""
    # `bash -c`, NOT `-lc`: a login shell re-sources /etc/profile and resets PATH to a minimal
    # set that DROPS the elan/cargo bins, so lake/cargo vanish. Non-login inherits the image's
    # ENV PATH (what `docker exec` provides), matching the harness's own tool invocations.
    code, out, err = exec_in(ctx.deps.container_id, ["bash", "-c", command],
                             workdir=workdir, timeout=timeout)
    body = out + (("\n──stderr──\n" + err) if err.strip() else "")
    if len(body) > _BASH_MAX:
        body = "…[output truncated — showing the last ~30k chars]…\n" + body[-_BASH_MAX:]
    log.info("bash: %r… (workdir=%s) → exit=%d, %d chars",
             command[:100].replace("\n", "⏎"), workdir, code, len(body))
    return f"exit={code}\n{body}"


def setup_lake_project(ctx: RunContext[AgentDeps]) -> str:
    """Wire the lake project around the Lean you generated so `import Aeneas` resolves offline.
    Call this AFTER running Aeneas into /workspace/out/lean; then `lake env lean <file>` (run it
    via bash from /workspace/out/lean) type-checks a single file. Returns a status/ERROR string."""
    from . import lean
    return lean.setup_lake(ctx.deps)


# ── harness file IO (plain values; not agent tools) ──────────────────────────

def read_out(deps: AgentDeps, path: str) -> str:
    """Read a generated file from /workspace/out. Returns ERROR: on failure."""
    path = _norm_out(path)
    code, out, err = exec_in(deps.container_id, ["cat", f"{OUT_IN}/{path}"], workdir=OUT_IN)
    if code != 0:
        return f"ERROR: cannot read '{path}': {err.strip()}"
    return out


def read_repo_sources(deps: AgentDeps) -> str:
    """Read all .rs files plus Cargo.toml and build.rs from the repo as one formatted block,
    suitable for injecting into a stage prompt. Files under target/ are excluded."""
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
