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

from . import git_ops
from .container import REPO_IN, OUT_IN, exec_in
from .state import AgentDeps

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


def read_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a Rust source file (repo-relative path). Returns ERROR: if the file is missing."""
    code, out, err = exec_in(ctx.deps.container_id, ["cat", f"{REPO_IN}/{path}"])
    if code != 0:
        return f"ERROR: cannot read '{path}': {err.strip()}"
    log.info("read_file: %s (%d chars)", path, len(out))
    return out


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


def append_file(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    """Append *content* to *path* in /workspace/out (creates it if absent). Returns ERROR: on failure."""
    path = _norm_out(path)
    if err := _guard_out(path):
        return err
    full = f"{OUT_IN}/{path}"
    exec_in(ctx.deps.container_id, ["mkdir", "-p", str(Path(full).parent)])
    if fail := _tee(ctx.deps.container_id, full, content, append=True):
        return f"ERROR: append_file failed for '{path}': {fail}"
    log.info("append_file: %s (+%d chars)", path, len(content))
    return f"Appended {len(content)} chars to {path}"


def run_aeneas(deps: AgentDeps, entry_file: str) -> dict:
    """Translate *entry_file* (repo-relative) to Lean 4 via Charon + Aeneas.

    The source is NEVER modified — Aeneas runs on the crate exactly as written, so the
    translation is a faithful image of the real code. Constructs Aeneas can't handle
    become explicit `sorry` holes (see `holes`), not failures and not silent rewrites.

    Returns a dict with:
      success      — True if Charon+Aeneas produced Lean output (holes are fine)
      lean_files   — list of generated Lean files (relative to /workspace/out)
      lean_path    — primary Lean file (the crate module) for downstream tools
      holes        — names of functions left untranslated (bare `sorry` body)
      charon_errors — Charon stderr (non-empty only on hard failure)
      aeneas_errors — parsed Aeneas error/warn lines
      commit       — git SHA if files were committed (empty string otherwise)

    success is False only on a HARD failure — Charon produced no `.llbc`, or Aeneas
    produced no `.lean` at all. There is no retry/rewrite path: if the crate can't be
    translated as written, that is reported, not worked around.
    """
    from . import config

    lean_out_dir = f"{OUT_IN}/lean"
    exec_in(deps.container_id, ["mkdir", "-p", lean_out_dir])

    # ── Step 1: Charon ────────────────────────────────────────────────────────
    log.info("Running Charon in %s", REPO_IN)
    charon_code, charon_out, charon_err = exec_in(
        deps.container_id,
        [config.CHARON_BIN, "cargo", "--preset=aeneas"],
        workdir=REPO_IN,
        timeout=600,
    )
    if charon_code != 0:
        log.warning("Charon exited %d", charon_code)
        return {
            "success": False,
            "lean_files": [], "lean_path": "", "holes": [],
            "charon_errors": charon_err,
            "aeneas_errors": [],
            "commit": "",
        }

    # Charon names the output after the crate; find the .llbc file it produced.
    _, llbc_list, _ = exec_in(
        deps.container_id,
        ["find", REPO_IN, "-maxdepth", "2", "-name", "*.llbc"],
        workdir=REPO_IN,
    )
    llbc_files = [l.strip() for l in llbc_list.splitlines() if l.strip()]
    if not llbc_files:
        log.warning("No .llbc file found after Charon run")
        return {
            "success": False,
            "lean_files": [], "lean_path": "", "holes": [],
            "charon_errors": "no .llbc file produced",
            "aeneas_errors": [],
            "commit": "",
        }
    llbc_path = llbc_files[0]
    log.info("Charon produced: %s", llbc_path)

    # ── Step 2: Aeneas ────────────────────────────────────────────────────────
    log.info("Running Aeneas on %s → %s", llbc_path, lean_out_dir)
    aeneas_code, aeneas_out, aeneas_err = exec_in(
        deps.container_id,
        [config.AENEAS_BIN, "-backend", "lean", "-dest", lean_out_dir, llbc_path],
        workdir=REPO_IN,
        timeout=600,
    )
    if aeneas_code != 0:
        log.warning("Aeneas exited %d", aeneas_code)

    # Parse Aeneas [Error]/[Warn] lines for the caller.
    aeneas_errors = [
        line.strip() for line in aeneas_err.splitlines()
        if "[Error]" in line or "Could not translate" in line
    ]

    _, lean_list, _ = exec_in(
        deps.container_id,
        ["find", lean_out_dir, "-name", "*.lean"],
        workdir=REPO_IN,
    )
    lean_files = [
        l.strip().removeprefix(OUT_IN + "/")
        for l in lean_list.splitlines()
        if l.strip() and not Path(l.strip()).name.startswith("._")
        and Path(l.strip()).name != "lakefile.lean"   # not a translation module
    ]
    log.info("Aeneas wrote %d Lean file(s): %s", len(lean_files), lean_files)

    if not lean_files:
        return {
            "success": False,
            "lean_files": [], "lean_path": "", "holes": [],
            "charon_errors": "",
            "aeneas_errors": aeneas_errors or [aeneas_err[:400]],
            "commit": "",
        }

    # Write a lakefile.lean so `lake build` works.  The generated Lean files
    # use `import Aeneas`, so we declare a path dependency on the bundled
    # Aeneas Lean library.  The package/lib name is derived from the crate name.
    _write_lakefile(deps, lean_out_dir, llbc_path)

    # Aeneas leaves functions it cannot translate as an explicit `sorry` body (a
    # "hole") rather than failing — e.g. iterator-adaptor chains, or a `main` doing
    # I/O.  Holes are NOT failures; the surrounding functions are translated faithfully.
    # Success = Lean output was produced (checked above); the source is never modified.
    holes = _detect_holes(deps, lean_files)
    sha = git_ops.commit(
        deps.container_id,
        f"feat(aeneas): translate {entry_file} → Lean"
        + (f" ({len(holes)} hole(s))" if holes else ""),
        glob="lean/",
    )
    log.info("Aeneas done — %d file(s), %d hole(s) — commit %s",
             len(lean_files), len(holes), sha[:8])
    # Primary module is the crate module (lean/<Crate>.lean), matching the lakefile's
    # lean_lib root — chosen deterministically rather than by find order.
    crate_module = f"lean/{Path(llbc_path).stem.capitalize()}.lean"
    lean_path = crate_module if crate_module in lean_files else lean_files[0]
    return {
        "success": True,
        "lean_files": lean_files,
        "lean_path": lean_path,
        "holes": holes,
        "charon_errors": "",
        "aeneas_errors": aeneas_errors,
        "commit": sha,
    }


def _def_blocks(text: str) -> dict[str, str]:
    """Map each `def NAME` to its source block (header through just before the next
    top-level `def`/`end`)."""
    import re
    blocks: dict[str, str] = {}
    for m in re.finditer(r"(?m)^def\s+([\w.]+)", text):
        rest = text[m.end():]
        nxt = re.search(r"(?m)^(def|end)\b", rest)
        end = m.end() + (nxt.start() if nxt else len(rest))
        blocks[m.group(1)] = text[m.start():end].rstrip()
    return blocks


def _strip_lean_comments(s: str) -> str:
    """Drop Lean block comments (incl. `/-- … -/` docstrings) and line comments, so a
    following def's docstring — swallowed into a block — can't create a false call edge."""
    import re
    s = re.sub(r"/-.*?-/", " ", s, flags=re.S)
    return re.sub(r"(?m)--.*$", " ", s)


def call_closure(text: str, roots: list[str]) -> list[str]:
    """Textual transitive closure: def names reachable from *roots* by reference in
    Lean *text* (comments stripped). Approximate (token-boundary match); over-
    approximation is the safe direction for the hole check. Precise LLBC-graph closure
    is a later refinement."""
    import re
    blocks = {n: _strip_lean_comments(b) for n, b in _def_blocks(text).items()}
    seen, stack = set(roots), list(roots)
    while stack:
        body = blocks.get(stack.pop(), "")
        for other in blocks:
            if other not in seen and re.search(r"(?<![\w])" + re.escape(other) + r"(?![\w])", body):
                seen.add(other)
                stack.append(other)
    return sorted(seen)


def closure_lean(text: str, names: list[str]) -> str:
    """Concatenated source of the given def blocks — the closure's Lean, for injection."""
    blocks = _def_blocks(text)
    return "\n\n".join(blocks[n] for n in names if n in blocks)


def _detect_holes(deps: AgentDeps, lean_files: list[str]) -> list[str]:
    """Names of functions Aeneas left untranslated (a bare `sorry` body)."""
    import re
    holes: list[str] = []
    for rel in lean_files:
        code, text, _ = exec_in(deps.container_id, ["cat", f"{OUT_IN}/{rel}"])
        if code != 0:
            continue
        for m in re.finditer(r"(?m)^def\s+([\w.]+)", text):
            start = m.end()
            nxt = re.search(r"(?m)^(def|end)\b", text[start:])
            block = text[start: start + (nxt.start() if nxt else len(text))]
            if re.search(r"(?m)^\s*sorry\s*$", block):
                holes.append(m.group(1))
    return holes


AENEAS_LEAN = "/opt/aeneas/backends/lean"
LEAN_TEMPLATE = "/opt/lean-template"


def _write_lakefile(deps: AgentDeps, lean_out_dir: str, llbc_path: str) -> None:
    """Generate a lakefile.lean and wire up the pre-resolved package manifest.

    The Docker image contains /opt/lean-template — a minimal lake project that
    already ran `lake update` against the bundled Aeneas runtime.  We copy its
    lake-manifest.json and symlink its .lake/packages so `lake build` works
    fully offline (--network none).
    """
    crate = Path(llbc_path).stem      # "fibonacci"
    lib_name = crate.capitalize()     # "Fibonacci"

    lakefile = (
        "import Lake\n"
        "open Lake DSL\n\n"
        f'require aeneas from "{AENEAS_LEAN}"\n\n'
        f'package «{crate}» where\n\n'
        f'lean_lib «{lib_name}» where\n'
    )

    def _exec(cmd_args: list[str]) -> None:
        subprocess.run(["docker", "exec", deps.container_id] + cmd_args,
                       capture_output=True, text=True, check=True)

    def _exec_input(cmd_args: list[str], stdin: str) -> None:
        subprocess.run(["docker", "exec", "--interactive", deps.container_id] + cmd_args,
                       input=stdin, capture_output=True, text=True, check=True)

    _exec_input(["tee", f"{lean_out_dir}/lakefile.lean"], lakefile)

    # Use the pre-resolved manifest and toolchain file from the template so lake
    # knows the pinned package set without any network access.
    _exec(["cp", f"{LEAN_TEMPLATE}/lake-manifest.json",
           f"{lean_out_dir}/lake-manifest.json"])
    _exec(["cp", f"{LEAN_TEMPLATE}/lean-toolchain",
           f"{lean_out_dir}/lean-toolchain"])

    # Symlink the pre-downloaded package trees (Mathlib, Aeneas runtime, etc.)
    _exec(["mkdir", "-p", f"{lean_out_dir}/.lake"])
    _exec(["ln", "-sfn", f"{LEAN_TEMPLATE}/.lake/packages",
           f"{lean_out_dir}/.lake/packages"])

    log.info("Generated lakefile.lean + package symlinks for crate '%s'", crate)


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


def git_log(ctx: RunContext[AgentDeps], n: int = 10) -> str:
    """Show the last *n* commits in /workspace/out."""
    return git_ops.log_oneline(ctx.deps.container_id, n=n)


def git_commit(ctx: RunContext[AgentDeps], message: str) -> str:
    """Stage all changes in /workspace/out and create a git commit."""
    return git_ops.commit(ctx.deps.container_id, message)


_BUILD_TAIL = 200  # lines of stderr to keep on failure — errors appear at the end


def check_lean(deps: AgentDeps, lean_file: str) -> dict:  # noqa: ARG001
    """Run `lake build` on the Lean project.

    Returns {"success": bool, "stderr": str}.  stdout is discarded (it carries no
    actionable info).  On failure, stderr is trimmed to the last 200 lines so only
    the relevant error messages reach the model.  On success, stderr is empty.
    """
    from . import config
    lean_out_dir = f"{OUT_IN}/lean"
    code, out, err = exec_in(deps.container_id, [config.LAKE_BIN, "build"],
                             workdir=lean_out_dir, timeout=1200)
    if code != 0:
        log.warning("lake build failed (exit %d)", code)
        log.debug("lake build stderr:\n%s", err)
        lines = err.splitlines()
        trimmed = "\n".join(lines[-_BUILD_TAIL:]) if len(lines) > _BUILD_TAIL else err
        return {"success": False, "stderr": trimmed}
    log.info("check_lean: lake build succeeded")
    return {"success": True, "stderr": ""}
