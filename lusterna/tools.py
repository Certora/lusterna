"""Agent tools — registered directly on pydantic-ai agents.

Public functions take RunContext[AgentDeps] and can be passed to agent.tool() without
any wrapper.  Private helpers (_norm_out, _guard_out, _tee, _write_lakefile) take plain
values and are called internally.  run_aeneas and check_lean are implementation helpers
called from agent.py wrappers that carry additional pipeline logic (progress, checkpoint).
"""
import logging
import os
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


def referenced_defs(spec_text: str, translation_text: str) -> list[str]:
    """Translation def names that *spec_text* mentions by name (comments stripped) — the
    seeds of a stated property's footprint. `call_closure` then expands them transitively.
    Approximate (token-boundary match); over-approximation is the safe direction for the
    hole check."""
    import re
    body = _strip_lean_comments(spec_text)
    return [
        name for name in _def_blocks(translation_text)
        if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", body)
    ]


def _theorem_names(spec_text: str) -> list[str]:
    """Names as written after `theorem`/`lemma` in the implementation spec."""
    import re
    return [m.group(2) for m in re.finditer(r"(?m)^\s*(theorem|lemma)\s+([\w.]+)", spec_text)]


def stub_proofs(text: str) -> str:
    """Force every `theorem`/`lemma` proof body to `:= by sorry`, preserving statements,
    definitions, imports and docstrings. FORMALISE emits statement-only structured output,
    so its theorems never carry proofs; this is a safety net for any stray theorem the
    model puts in the free-form `preamble` — keeping proofs (and pathological tactics like
    `native_decide`) out of the spec until the PROVE stage."""
    import re
    lines = text.split("\n")
    decl = re.compile(r"^\s*(theorem|lemma)\b")
    newtop = re.compile(r"^\s*(theorem|lemma|def|abbrev|noncomputable|instance|structure|"
                        r"inductive|namespace|end|section|open|variable|@\[|/-|--|#|import)")
    out, i, n = [], 0, len(lines)
    while i < n:
        if decl.match(lines[i]):
            block = [lines[i]]
            i += 1
            while i < n and not newtop.match(lines[i]):
                block.append(lines[i]); i += 1
            joined = "\n".join(block)
            m = re.search(r":=", joined)
            out.append((joined[:m.start()].rstrip() + " := by sorry") if m else joined)
        else:
            out.append(lines[i]); i += 1
    return "\n".join(out)


_STD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}  # the standard, trusted Lean axioms


def check_axioms(deps: AgentDeps, spec_rel: str) -> dict:
    """Authoritative hole-impact oracle. Ask Lean which impl-spec theorems are GENUINELY
    established — i.e. whose proof term depends on NOTHING beyond the standard trusted axioms
    (propext, Classical.choice, Quot.sound). This subsumes the textual footprint check and the
    sorry-count: an untranslated Aeneas hole and an unfinished proof BOTH introduce `sorryAx`,
    and `#print axioms` follows the real proof term through simp sets, instances and every
    definition — closing the blind spot of the name-based footprint approximation. Crucially it
    also rejects any OTHER non-standard axiom: `sorryAx`, `Lean.ofReduceBool`/`Lean.trustCompiler`
    (native_decide's compiler trust), and any `axiom` the model might smuggle in all taint a
    theorem — "established" means kernel-checked with only the standard axioms.

    Returns {"clean": [names], "tainted": [names], "raw": <trimmed lean output>}, where
    tainted = depends on a non-standard axiom OR could not be resolved (the conservative direction).

    Mechanism: write a throwaway checker that IMPORTS the already-built `Spec.olean` and
    runs `#print axioms` against it, then elaborate just that checker with `lake env lean`.
    The spec is never re-elaborated — no proofs (or `native_decide`) rerun, and imports
    resolve from the compiled artifacts the pipeline already built. The checker is deleted
    afterwards. (Re-elaborating the spec from source instead is fragile: one import or
    proof failure auto-`sorry`s every declaration and taints the whole batch.)
    """
    import re
    from . import config
    original = read_out(deps, spec_rel)
    if original.startswith("ERROR:"):
        return {"clean": [], "tainted": [], "raw": original}
    names = _theorem_names(original)
    if not names:
        return {"clean": [], "tainted": [], "raw": ""}

    # spec_rel = lean/<Lib>/Spec.lean  →  spec module <Lib>.Spec (matches the lean_lib root)
    parts = _norm_out(spec_rel).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return {"clean": [], "tainted": [], "raw": f"ERROR: unexpected spec path {spec_rel!r}"}
    spec_module = ".".join(parts[1:]).removesuffix(".lean")

    checker_rel = "_axiom_check.lean"          # at out root — outside the lean_lib srcDir
    body = f"import {spec_module}\n\n" + "\n".join(f"#print axioms {n}" for n in names) + "\n"
    if (w := write_out(deps, checker_rel, body)).startswith("ERROR:"):
        return {"clean": [], "tainted": [], "raw": w}
    _, out, err = exec_in(deps.container_id,
                          ["timeout", "-k", "10", str(_BUILD_TIMEOUT),
                           config.LAKE_BIN, "env", "lean", f"{OUT_IN}/{checker_rel}"],
                          workdir=f"{OUT_IN}/lean", timeout=_BUILD_TIMEOUT + 30)
    exec_in(deps.container_id, ["rm", "-f", f"{OUT_IN}/{checker_rel}"])

    text = f"{out}\n{err}"
    # Each verdict line is either "'name' does not depend on any axioms" (clean) or
    # "'name' depends on axioms: [a, b, ...]" — clean iff every listed axiom is standard.
    verdict: dict[str, bool] = {}
    for line in text.splitlines():
        m = re.search(r"'([\w.]+)' (?:does not depend|depends on axioms: \[([^\]]*)\])", line)
        if not m:
            continue
        short = m.group(1).split(".")[-1]
        if m.group(2) is None:          # "does not depend on any axioms"
            verdict[short] = True
        else:
            axes = [a.strip() for a in m.group(2).split(",") if a.strip()]
            verdict[short] = all(a in _STD_AXIOMS for a in axes)
    clean = [n for n in names if verdict.get(n.split(".")[-1]) is True]
    tainted = [n for n in names if verdict.get(n.split(".")[-1]) is not True]  # False or unresolved
    unresolved = [n for n in names if n.split(".")[-1] not in verdict]
    if unresolved:
        # Not a soundness signal — the checker couldn't read these back. Surface it loudly
        # instead of silently reporting them as tainted.
        log.warning("check_axioms: %d/%d theorem(s) UNRESOLVED (conservatively tainted, not a "
                    "real axiom finding): %s — lean output tail:\n%s",
                    len(unresolved), len(names), unresolved, text[-1200:])
    log.info("check_axioms: %d established (only standard axioms), %d tainted, of %d theorem(s)",
             len(clean), len(tainted), len(names))
    return {"clean": clean, "tainted": tainted, "raw": text[-3000:]}


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

    # `@[default_target]` is essential: without it, a bare `lake build` (what check_lean
    # runs) builds NOTHING ("0 jobs") and returns success — silently disconnecting the
    # build oracle so broken specs/proofs pass unchecked. `globs := .andSubmodules` makes
    # the lib build ALL its modules (crucially Fibonacci.Spec, which the root module does
    # not import), so the implementation spec is actually compiled.
    lakefile = (
        "import Lake\n"
        "open Lake DSL\n\n"
        f'require aeneas from "{AENEAS_LEAN}"\n\n'
        f'package «{crate}» where\n\n'
        f'@[default_target]\n'
        f'lean_lib «{lib_name}» where\n'
        f'  globs := #[.andSubmodules `{lib_name}]\n'
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
# A legitimate build is seconds; this hard cap fails-fast on a pathological tactic
# (e.g. `native_decide` evaluating naive recursion) instead of pegging a core for 20 min.
_BUILD_TIMEOUT = int(os.environ.get("LUSTERNA_BUILD_TIMEOUT", "180"))


def check_lean(deps: AgentDeps, lean_file: str) -> dict:  # noqa: ARG001
    """Run `lake build` on the Lean project.

    Returns {"success": bool, "stderr": str}, where on failure "stderr" holds the actionable
    build DIAGNOSTICS. Note: `lake build` prints the per-declaration errors
    (`error: <file>:<line>:<col>: ...`) to STDOUT and only a terse `error: build failed` to
    real stderr — so we combine stdout+stderr and strip the `trace:`/`✖`-prefixed build-log
    decoration, keeping the last 200 lines (errors are at the end). On success "stderr" is empty.
    """
    from . import config
    lean_out_dir = f"{OUT_IN}/lean"
    # Wrap in a container-side `timeout` so a runaway tactic (e.g. `native_decide` evaluating
    # naive recursion) is KILLED inside the container — killing only the host-side docker-exec
    # client would leave the build burning a core. `-k 10` escalates to SIGKILL if needed. The
    # host-side timeout is a slightly-larger backstop.
    code, out, err = exec_in(
        deps.container_id,
        ["timeout", "-k", "10", str(_BUILD_TIMEOUT), config.LAKE_BIN, "build"],
        workdir=lean_out_dir, timeout=_BUILD_TIMEOUT + 30)
    if code in (124, 137):
        log.warning("lake build exceeded %ds and was terminated", _BUILD_TIMEOUT)
        return {"success": False, "stderr": (
            f"lake build exceeded the {_BUILD_TIMEOUT}s limit and was terminated — most "
            "likely a tactic that evaluates a recursive definition (e.g. `native_decide`/"
            "`decide` on a naive `fib`). Treat this as a failed attempt: prove by reasoning "
            "(induction / equation lemmas) or leave the theorem as `sorry`.")}
    if code != 0:
        log.warning("lake build failed (exit %d)", code)
        # Diagnostics are on STDOUT; strip the noisy build-log lines (`trace: .> …` command
        # echoes and `✖ [k/n] Building …` headers) and keep stdout+stderr.
        diag = [ln for ln in (out + "\n" + err).splitlines()
                if not ln.startswith("trace:") and not ln.lstrip().startswith("✖")]
        log.debug("lake build diagnostics:\n%s", "\n".join(diag))
        trimmed = "\n".join(diag[-_BUILD_TAIL:])
        return {"success": False, "stderr": trimmed}
    log.info("check_lean: lake build succeeded")
    return {"success": True, "stderr": ""}


if __name__ == "__main__":
    # Worked example of the footprint idea: a hole taints a property ONLY when it lies in
    # the closure of the defs that property references. Run: `python -m lusterna.tools`.
    _TRANSLATION = """
def foo.helper (x : Nat) : Nat := x + 1
def foo.compute (x : Nat) : Nat := foo.helper x
def foo.untranslatable (x : Nat) : Nat :=
  sorry
def foo.other (x : Nat) : Nat := foo.untranslatable x
"""
    _HOLES = ["foo.untranslatable"]  # what Aeneas left as a bare `sorry`
    for label, spec in [
        ("clean   (property touches compute → helper)", "theorem t : foo.compute 0 = 1 := by sorry"),
        ("tainted (property reaches other → untranslatable)", "theorem t : foo.other 0 = 0 := by sorry"),
    ]:
        roots = referenced_defs(spec, _TRANSLATION)
        footprint = call_closure(_TRANSLATION, roots)
        holes_in_footprint = [h for h in _HOLES if h in footprint]
        verdict = "SOUND" if not holes_in_footprint else f"NOT SOUND — holes {holes_in_footprint}"
        print(f"{label}\n  roots={roots} footprint={footprint} → {verdict}\n")
