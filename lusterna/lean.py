"""Aeneas/Lean operations and analysis: translation, build, axiom check, call-closures,
footprint, and spec assembly helpers. The heavy Lean logic, kept out of the trivial file
tools in tools.py."""
import logging
import os
import re
import subprocess
from pathlib import Path

from . import checkpoint, config, tools
from .container import exec_in, OUT_IN, REPO_IN
from .schemas import AgentDeps, FormalSpec

log = logging.getLogger(__name__)


def run_aeneas(deps: AgentDeps, entry_file: str, *,
               start_from: list[str] | None = None,
               opaque: list[str] | None = None,
               exclude: list[str] | None = None,
               include: list[str] | None = None) -> dict:
    """Translate *entry_file* (repo-relative) to Lean 4 via Charon + Aeneas.

    Called with no scoping options this is byte-identical to the historical whole-crate
    translation. The optional Charon name-matcher scoping (used by the TRANSLATE
    remediation loop) narrows/adjusts what enters the `.llbc`:
      start_from — translate only these items and their call-closure (default: whole crate)
      opaque     — keep these items opaque; Aeneas emits them as a Lean `axiom` (an explicit
                   assumption the `#print axioms` gate then flags on any dependent theorem)
      exclude    — do not translate these items at all
      include    — whitelist refinement (name-matcher; most-precise pattern wins)

    run_aeneas itself never edits the Rust source — it only translates whatever is present.
    Constructs Aeneas can't handle become explicit `sorry` holes (see `holes`).

    Returns a dict with:
      success       — True if Charon+Aeneas produced Lean output (holes are fine)
      stage_failed  — ""|"charon"|"aeneas": which stage hard-failed (for the remediation loop)
      lean_files    — list of generated Lean files (relative to /workspace/out)
      lean_path     — primary Lean file (the crate module) for downstream tools
      holes         — flat list of untranslated function names (bare `sorry` body)
      holes_by_file — {lean_file: [hole names]} — per-file attribution
      charon_errors — Charon stderr (non-empty only on hard failure)
      aeneas_errors — parsed Aeneas error/warn lines
      scope         — {start_from, opaque, exclude, include}: the options used
      commit        — git SHA if files were committed (empty string otherwise)

    success is False only on a HARD failure — Charon produced no `.llbc`, or Aeneas
    produced no `.lean` at all.
    """
    from . import config

    scope = {
        "start_from": list(start_from or []),
        "opaque": list(opaque or []),
        "exclude": list(exclude or []),
        "include": list(include or []),
    }

    def _fail(stage: str, charon_errors: str = "", aeneas_errors: list | None = None) -> dict:
        return {
            "success": False, "stage_failed": stage,
            "lean_files": [], "lean_path": "", "holes": [], "holes_by_file": {},
            "charon_errors": charon_errors, "aeneas_errors": aeneas_errors or [],
            "commit": "",
        }

    lean_out_dir = f"{OUT_IN}/lean"
    # Start each attempt from a clean slate so stale Lean/.llbc from a previous
    # remediation attempt cannot pollute lean_files / hole detection.
    exec_in(deps.container_id, ["rm", "-rf", lean_out_dir])
    exec_in(deps.container_id, ["mkdir", "-p", lean_out_dir])
    exec_in(deps.container_id,
            ["find", REPO_IN, "-maxdepth", "2", "-name", "*.llbc", "-delete"],
            workdir=REPO_IN)

    # ── Step 1: Charon ────────────────────────────────────────────────────────
    # Direct argv (NOT a shell): scoping flags are appended, one repetition per pattern.
    charon_cmd = [config.CHARON_BIN, "cargo", "--preset=aeneas"]
    for flag, key in (("--start-from", "start_from"), ("--opaque", "opaque"),
                      ("--exclude", "exclude"), ("--include", "include")):
        for pat in scope[key]:
            charon_cmd += [flag, pat]
    log.info("Running Charon in %s (scope=%s)", REPO_IN,
             {k: v for k, v in scope.items() if v} or "whole-crate")
    charon_code, charon_out, charon_err = exec_in(
        deps.container_id,
        charon_cmd,
        workdir=REPO_IN,
        timeout=600,
    )
    if charon_code != 0:
        log.warning("Charon exited %d", charon_code)
        return _fail("charon", charon_errors=charon_err)

    # Charon names the output after the crate; find the .llbc file it produced.
    _, llbc_list, _ = exec_in(
        deps.container_id,
        ["find", REPO_IN, "-maxdepth", "2", "-name", "*.llbc"],
        workdir=REPO_IN,
    )
    llbc_files = [l.strip() for l in llbc_list.splitlines() if l.strip()]
    if not llbc_files:
        log.warning("No .llbc file found after Charon run")
        return _fail("charon", charon_errors="no .llbc file produced")
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
        return _fail("aeneas", aeneas_errors=aeneas_errors or [aeneas_err[:400]])

    # The crate module is the top-level `lean/<Module>.lean` Aeneas emits (submodules live
    # under `lean/<Module>/`). Derive the lib name from that ACTUAL file — Aeneas CamelCases
    # multi-word crates (`erc20_rs` → `Erc20Rs`), which `str.capitalize()` gets wrong
    # (`Erc20_rs`), breaking `lake build`'s `andSubmodules` glob.
    top_level = [f for f in lean_files
                 if f.startswith("lean/") and "/" not in f[len("lean/"):]]
    crate_module = top_level[0] if top_level else lean_files[0]
    lib_name = Path(crate_module).stem

    # Write a lakefile.lean so `lake build` works.  The generated Lean files
    # use `import Aeneas`, so we declare a path dependency on the bundled
    # Aeneas Lean library.  The lean_lib name MUST match the generated module.
    _write_lakefile(deps, lean_out_dir, llbc_path, lib_name)

    # Aeneas leaves functions it cannot translate as an explicit `sorry` body (a
    # "hole") rather than failing — e.g. iterator-adaptor chains, or a `main` doing
    # I/O.  Holes are NOT failures; the surrounding functions are translated faithfully.
    # Success = Lean output was produced (checked above); the source is never modified.
    holes_by_file = _detect_holes(deps, lean_files)
    holes = sorted({h for hs in holes_by_file.values() for h in hs})
    bits = [f"{k}={','.join(v)}" for k, v in scope.items() if v]
    scope_suffix = f" [{'; '.join(bits)}]" if bits else ""
    sha = tools.commit(
        deps.container_id,
        f"feat(aeneas): translate {entry_file} → Lean"
        + scope_suffix
        + (f" ({len(holes)} hole(s))" if holes else ""),
        glob="lean/",
    )
    log.info("Aeneas done — %d file(s), %d hole(s) — commit %s",
             len(lean_files), len(holes), sha[:8])
    return {
        "success": True,
        "stage_failed": "",
        "lean_files": lean_files,
        "lean_path": crate_module,
        "holes": holes,
        "holes_by_file": holes_by_file,
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


def _mentions(name: str, text: str) -> bool:
    """True if *name* occurs in *text* as a whole token (Lean identifier boundary)."""
    return bool(re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text))


def call_closure(text: str, roots: list[str]) -> list[str]:
    """Textual transitive closure: def names reachable from *roots* by reference in
    Lean *text* (comments stripped). Approximate (token-boundary match); over-
    approximation is the safe direction for the hole check. Precise LLBC-graph closure
    is a later refinement."""
    blocks = {n: _strip_lean_comments(b) for n, b in _def_blocks(text).items()}
    seen, stack = set(roots), list(roots)
    while stack:
        body = blocks.get(stack.pop(), "")
        for other in blocks:
            if other not in seen and _mentions(other, body):
                seen.add(other)
                stack.append(other)
    return sorted(seen)


def referenced_defs(spec_text: str, translation_text: str) -> list[str]:
    """Translation def names that *spec_text* mentions by name (comments stripped) — the
    seeds of a stated property's footprint. `call_closure` then expands them transitively.
    Approximate (token-boundary match); over-approximation is the safe direction for the
    hole check."""
    body = _strip_lean_comments(spec_text)
    return [name for name in _def_blocks(translation_text) if _mentions(name, body)]


def _pattern_leaf(pattern: str) -> str:
    """Final identifier of a Charon name-matcher pattern, e.g. `crate::m::fib` → `fib`,
    `crate::Foo::_` → `Foo`. Drops trailing wildcards and any `{impl …}` decoration."""
    p = pattern.strip().split("{")[0]            # drop `{impl …}` blocks
    segs = [s for s in re.split(r"::", p) if s and s != "_"]
    return segs[-1] if segs else ""


def matched_target_defs(translation_text: str, target_patterns: list[str]) -> list[str]:
    """Translation def names that a target pattern names, matched by final identifier
    (Aeneas mangles `crate::fib` → `crate.fib`). Fuzzy/over-approximate: every def whose
    final `.`-component equals a pattern leaf is a seed. Empty ⇒ no pattern matched."""
    leaves = {_pattern_leaf(p) for p in target_patterns if _pattern_leaf(p)}
    if not leaves:
        return []
    return sorted(
        name for name in _def_blocks(translation_text)
        if name.split(".")[-1] in leaves
    )


def target_closure(translation_text: str, target_patterns: list[str]) -> list[str]:
    """Call-closure of the target seeds within the translation. Empty if no seed matched
    (the caller treats a matched-but-empty closure as a scoping mismatch, not 'all clear')."""
    seeds = matched_target_defs(translation_text, target_patterns)
    return call_closure(translation_text, seeds) if seeds else []


def target_holes(translation_text: str, holes: list[str], target_patterns: list[str]) -> list[str]:
    """Which untranslated holes fall inside the target's call-closure — the holes that
    actually block verification of the target. Conservative when scope is unknown: with no
    patterns (whole-crate) or no seed match, EVERY hole counts as target-relevant."""
    if not target_patterns:
        return sorted(holes)
    closure = set(target_closure(translation_text, target_patterns))
    if not closure:
        return sorted(holes)          # patterns didn't match — be conservative
    return sorted(h for h in holes if h in closure)


def external_axioms(translation_text: str) -> list[str]:
    """Top-level `axiom` names in the translation. Aeneas emits `axiom` ONLY for opaque
    external items (auto-opaqued stdlib like `BTreeMap`, `Option::ok_or`, trait impls); the
    crate's own items are `def`/`structure`/`inductive`. Any such axiom a property depends on
    would taint it under `#print axioms`, so for verification purposes it blocks like a hole."""
    return sorted({m.group(1) for m in re.finditer(r"(?m)^axiom\s+([\w.]+)", translation_text)})


# Formatting-only externals never affect observable behaviour, so a property can never
# meaningfully depend on them — exclude them from the blocker set. Anchored to Aeneas's actual
# naming: the `CoreFmtDebug`/`CoreFmtDisplay` trait-impl instances and any `.fmt` method — NOT
# an unanchored substring (which would wrongly catch e.g. `crate.DisplayConfig`).
_BENIGN_AXIOM = re.compile(r"CoreFmt(?:Debug|Display)|\.fmt$")


def target_external_axioms(translation_text: str, target_patterns: list[str]) -> list[str]:
    """Behaviour-relevant external axioms the target's call-closure depends on. Formatting-only
    externals (Debug/Display) are excluded — they can never be in a property's footprint. If the
    target seeds match no def (unscoped / whole-crate), every external axiom is in scope."""
    axioms = [a for a in external_axioms(translation_text) if not _BENIGN_AXIOM.search(a)]
    if not axioms:
        return []
    closure = set(target_closure(translation_text, target_patterns))
    if not closure:
        return sorted(axioms)
    bodies = [_strip_lean_comments(b) for n, b in _def_blocks(translation_text).items()
              if n in closure]
    return sorted(ax for ax in axioms if any(_mentions(ax, b) for b in bodies))


def trail_opaque_assumptions(trail: list[dict]) -> list[str]:
    """Charon patterns made `--opaque` across the TRANSLATE trail — emitted as Lean axioms,
    so the `#print axioms` gate flags any theorem depending on them."""
    return sorted({pat for r in trail if r.get("action") == "OPAQUE"
                   for pat in r.get("scope", {}).get("opaque", [])})


def trail_refactored_paths(trail: list[dict]) -> list[str]:
    """Source files a behaviour-preserving refactor actually edited across the TRANSLATE trail."""
    return sorted({se["path"] for r in trail if r.get("action") == "REFACTOR"
                   for se in r.get("source_edits", []) if se.get("applied")})


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
    original = tools.read_out(deps, spec_rel)
    if original.startswith("ERROR:"):
        return {"clean": [], "tainted": [], "raw": original}
    names = _theorem_names(original)
    if not names:
        return {"clean": [], "tainted": [], "raw": ""}

    # spec_rel = lean/<Lib>/Spec.lean  →  spec module <Lib>.Spec (matches the lean_lib root)
    parts = tools._norm_out(spec_rel).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return {"clean": [], "tainted": [], "raw": f"ERROR: unexpected spec path {spec_rel!r}"}
    spec_module = ".".join(parts[1:]).removesuffix(".lean")

    checker_rel = "_axiom_check.lean"          # at out root — outside the lean_lib srcDir
    body = f"import {spec_module}\n\n" + "\n".join(f"#print axioms {n}" for n in names) + "\n"
    if (w := tools.write_out(deps, checker_rel, body)).startswith("ERROR:"):
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


def _detect_holes(deps: AgentDeps, lean_files: list[str]) -> dict[str, list[str]]:
    """Map each Lean file to the names of functions Aeneas left untranslated (a bare
    `sorry` body). A file with no holes is omitted from the map."""
    holes_by_file: dict[str, list[str]] = {}
    for rel in lean_files:
        code, text, _ = exec_in(deps.container_id, ["cat", f"{OUT_IN}/{rel}"])
        if code != 0:
            continue
        file_holes = [name for name, block in _def_blocks(text).items()
                      if re.search(r"(?m)^\s*sorry\s*$", block)]
        if file_holes:
            holes_by_file[rel] = file_holes
    return holes_by_file


AENEAS_LEAN = "/opt/aeneas/backends/lean"
LEAN_TEMPLATE = "/opt/lean-template"


def _write_lakefile(deps: AgentDeps, lean_out_dir: str, llbc_path: str, lib_name: str) -> None:
    """Generate a lakefile.lean and wire up the pre-resolved package manifest.

    The Docker image contains /opt/lean-template — a minimal lake project that
    already ran `lake update` against the bundled Aeneas runtime.  We copy its
    lake-manifest.json and symlink its .lake/packages so `lake build` works
    fully offline (--network none).

    *lib_name* is the ACTUAL generated module name (Aeneas CamelCases multi-word crates,
    e.g. `erc20_rs` → `Erc20Rs`); the lean_lib name/glob must match it exactly or
    `lake build`'s `andSubmodules` glob fails to find the module.
    """
    crate = Path(llbc_path).stem      # package name, e.g. "erc20_rs" / "fibonacci"

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


_BUILD_TAIL = 200  # lines of stderr to keep on failure — errors appear at the end
# A legitimate build is seconds; this hard cap fails-fast on a pathological tactic
# (e.g. `native_decide` evaluating naive recursion) instead of pegging a core for 20 min.
_BUILD_TIMEOUT = int(os.environ.get("LUSTERNA_BUILD_TIMEOUT", "180"))


def _run_lake(deps: AgentDeps, args: list[str], timeout_msg: str) -> dict:
    """Run `lake <args>` in the Lean project under a container-side timeout and return
    {"success": bool, "stderr": str}. The `-k 10` timeout KILLS a runaway build inside the
    container (a host-side kill would leave it burning a core). On non-zero exit, "stderr"
    holds the actionable diagnostics: `lake` prints per-declaration errors to STDOUT (only a
    terse `error: build failed` to real stderr), so we combine both streams, strip the
    `trace:`/`✖` build-log decoration, and keep the last _BUILD_TAIL lines (errors are at the
    end). On success "stderr" is empty; on timeout it holds *timeout_msg*."""
    from . import config
    code, out, err = exec_in(
        deps.container_id,
        ["timeout", "-k", "10", str(_BUILD_TIMEOUT), config.LAKE_BIN, *args],
        workdir=f"{OUT_IN}/lean", timeout=_BUILD_TIMEOUT + 30)
    if code in (124, 137):
        log.warning("lake %s exceeded %ds and was terminated", args[0], _BUILD_TIMEOUT)
        return {"success": False, "stderr": timeout_msg}
    if code != 0:
        log.warning("lake %s failed (exit %d)", args[0], code)
        diag = [ln for ln in (out + "\n" + err).splitlines()
                if not ln.startswith("trace:") and not ln.lstrip().startswith("✖")]
        return {"success": False, "stderr": "\n".join(diag[-_BUILD_TAIL:])}
    return {"success": True, "stderr": ""}


def check_lean(deps: AgentDeps, lean_file: str) -> dict:  # noqa: ARG001
    """Run `lake build` on the Lean project (the build oracle). Returns {"success", "stderr"};
    on failure "stderr" holds the build diagnostics."""
    r = _run_lake(deps, ["build"], timeout_msg=(
        f"lake build exceeded the {_BUILD_TIMEOUT}s limit and was terminated — most likely a "
        "tactic that evaluates a recursive definition (e.g. `native_decide`/`decide` on a naive "
        "`fib`). Treat this as a failed attempt: prove by reasoning (induction / equation "
        "lemmas) or leave the theorem as `sorry`."))
    if r["success"]:
        log.info("check_lean: lake build succeeded")
    return r


def translation_compiles(deps: AgentDeps, lean_path: str) -> dict:
    """Typecheck a single translation file via `lake env lean <file>` — NOT `lake build`.

    At TRANSLATE time the lakefile's `globs := .andSubmodules` target needs the `<Crate>/`
    submodule directory (created later by FORMALISE's Spec.lean); it does not exist yet, so
    `lake build` would fail on the glob, not on the code. `lake env lean` compiles just this
    file in the project environment (imports resolve against the prebuilt Aeneas packages)."""
    return _run_lake(deps, ["env", "lean", f"{OUT_IN}/{lean_path}"],
                     timeout_msg=f"lake env lean exceeded {_BUILD_TIMEOUT}s")


def attribute_errors(spec_text: str, stderr: str, basename: str = "Spec.lean") -> dict:
    """Map Lean build errors back to the impl-spec theorem they occur in, so FORMALISE can be
    told exactly which statements to fix (and which already compile).

    `lake build` diagnostics are `<severity>: <path>:<line>:<col>: <msg>` (severity FIRST), with
    the message continuing on following lines until the next diagnostic or a lake/lean structural
    line. (`lake env lean` uses path-first; FORMALISE's oracle is `lake build`, so we match that.)
    `sorry` produces a WARNING, not an error — so a stub that type-checks shows only a warning;
    only an `error` marks a theorem as failing. Returns
    {"failing": {theorem: msg}, "preamble": [msg], "unattributed": [msg]}."""
    import re
    thm = list(re.finditer(r"(?m)^theorem\s+([\w.]+)", spec_text))
    nlines = spec_text.count("\n") + 1
    spans = []
    for i, m in enumerate(thm):
        start = spec_text.count("\n", 0, m.start()) + 1
        end = spec_text.count("\n", 0, thm[i + 1].start()) if i + 1 < len(thm) else nlines
        spans.append((m.group(1), start, end))
    first_thm = spans[0][1] if spans else nlines + 1

    def owner(line: int) -> str | None:
        return next((n for n, s, e in spans if s <= line <= e), None)

    marker = re.compile(r"^(error|warning): (\S+):(\d+):(\d+): (.*)$")
    stop = re.compile(r"^(?:error|warning|info|trace):|^\s*✖|^Some required|^- ")
    failing: dict[str, list[str]] = {}
    preamble: list[str] = []
    unattributed: list[str] = []
    lines = stderr.splitlines()
    i = 0
    while i < len(lines):
        mm = marker.match(lines[i])
        if not mm or not mm.group(2).endswith(basename):   # only diagnostics for the impl spec
            i += 1
            continue
        sev, ln = mm.group(1), int(mm.group(3))
        block = [f"{basename}:{ln}:{mm.group(4)}: {mm.group(5)}".rstrip()]   # path-stripped
        j = i + 1
        while j < len(lines) and not marker.match(lines[j]) and not stop.match(lines[j]):
            block.append(lines[j]); j += 1
        i = j
        if sev != "error":                              # ignore sorry/other warnings
            continue
        msg = "\n".join(block).strip()
        if (who := owner(ln)) is not None:
            failing.setdefault(who, []).append(msg)
        elif ln < first_thm:
            preamble.append(msg)
        else:
            unattributed.append(msg)
    return {"failing": {k: "\n".join(v) for k, v in failing.items()},
            "preamble": preamble, "unattributed": unattributed}


def theorem_statement(spec_text: str, name: str) -> str:
    """The statement text of theorem *name* — binders + proposition, up to (not including) `:=`.
    Used to decide whether a theorem references the implementation (referenced_defs on it).
    Returns '' if not found."""
    import re
    m = re.search(rf"(?m)^theorem\s+{re.escape(name)}\b", spec_text)
    if not m:
        return ""
    tail = spec_text[m.start():]
    cut = tail.find(":=")
    return tail[:cut] if cut != -1 else tail.split("\n\n", 1)[0]


# ── implementation-spec operations ─────────────────────────────────────────────

def impl_spec(deps: AgentDeps) -> str:
    """Canonical implementation-spec path — the single file FORMALISE fills and PROVE
    proves. Placed under the Aeneas lib root (lean/<Crate>/) so the existing lakefile
    builds it with no lakefile changes. Deterministic, never agent-chosen."""
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    if not lean_path:
        return ""
    stem = lean_path.rsplit("/", 1)[-1].removesuffix(".lean")
    return f"lean/{stem}/Spec.lean"


def assemble_impl_spec(deps: AgentDeps, fs: FormalSpec,
                        drop: frozenset[str] = frozenset()) -> list[str]:
    """Write the implementation spec from a structured FormalSpec: the preamble followed by
    one `theorem <name> <signature> := by sorry` per stub. Proofs are added here as `sorry`
    — never by the model — so FORMALISE cannot smuggle in a proof (or a hanging tactic).
    The preamble is passed through stub_proofs as a safety net for any stray theorem.

    Theorems whose name is in *drop* (quarantined — repeatedly un-compilable) are omitted.
    Returns the list of theorem names actually written, so the caller can attribute build
    errors back to specific theorems and track the quarantine set."""
    path = impl_spec(deps)
    stem = path.split("/")[1]
    preamble = stub_proofs(fs.preamble.rstrip())
    header = "".join(
        f"import {m}\n" for m in ("Aeneas", stem) if f"import {m}" not in preamble
    )
    kept = [t for t in fs.theorems if t.name not in drop]
    body = "\n\n".join(
        f"theorem {t.name} {t.signature.split(':=')[0].strip()} := by sorry"
        for t in kept
    )
    tools.write_out(deps, path, f"{header}{preamble}\n\n{body}\n")
    log.info("Assembled impl spec: %d theorem stub(s)%s at %s", len(kept),
             f" ({len(drop)} quarantined)" if drop else "", path)
    return [t.name for t in kept]


def build(deps: AgentDeps) -> dict:
    """Run `lake build` on the impl spec and record it as PROVE's progress metric does."""
    result = check_lean(deps, impl_spec(deps))
    deps.progress["lean_build"] = result
    deps.progress["build_seq"] = deps.progress.get("build_seq", 0) + 1
    checkpoint.snapshot(deps)
    return result


def sorry_count(deps: AgentDeps) -> int:
    """Remaining `sorry` in the implementation spec — PROVE's progress metric.
    Returns -1 if the file can't be read, so the caller never mistakes it for done."""
    content = tools.read_out(deps, impl_spec(deps))
    return -1 if content.startswith("ERROR:") else content.count("sorry")


def record_prove_best(deps: AgentDeps) -> None:
    """Snapshot the impl spec as PROVE's best state iff it COMPILES (caller checked) and reached
    a new `sorry` minimum. Only build-verified states count: a failing tactic removes a `sorry`
    but does not compile, so raw sorry-count is not progress — a compiling snapshot is. PROVE
    restores this at the end, so it always finalizes on its best verified state, never a later
    broken edit. progress['prove_best'] = {'sorry': n, 'spec': <content>}."""
    spec = tools.read_out(deps, impl_spec(deps))
    if spec.startswith("ERROR:"):
        return
    n = spec.count("sorry")
    best = deps.progress.get("prove_best")
    if best is None or n < best["sorry"]:
        deps.progress["prove_best"] = {"sorry": n, "spec": spec}
        log.info("PROVE: new best compiling spec — %d sorry remaining", n)


def translation_text(deps: AgentDeps, lean_files: list[str] | None = None) -> str:
    """Concatenated Lean source of the Aeneas translation — the material the spec stages
    reason over. Defaults to the committed translation in progress['aeneas']['lean_files'];
    pass *lean_files* to read an in-flight result (e.g. a TRANSLATE remediation attempt not
    yet stored in progress)."""
    if lean_files is None:
        lean_files = deps.progress.get("aeneas", {}).get("lean_files", [])
    parts = [t for rel in lean_files
             for t in [tools.read_out(deps, rel)] if not t.startswith("ERROR:")]
    return "\n\n".join(parts)


def footprint(deps: AgentDeps) -> dict:
    """Compute the footprint of the current implementation spec: the translation defs its
    theorems (statements + proofs) reference transitively, and which Aeneas holes fall
    inside it.

    This is the SOLE role of the 'unit' concept — a stated property is soundly grounded
    iff no hole lies in its footprint. Holes elsewhere in the crate are irrelevant to it.
    Recomputed after FORMALISE and after PROVE (proofs may pull in more defs). Stored in
    progress['footprint'] = {defs, holes_in_footprint}."""
    translation = translation_text(deps)
    spec = tools.read_out(deps, impl_spec(deps))
    holes = deps.progress.get("aeneas", {}).get("holes", [])
    if translation.startswith("ERROR:") or spec.startswith("ERROR:"):
        # Can't compute — take the conservative (never-claim-sound) direction.
        fp = {"defs": [], "holes_in_footprint": list(holes)}
    else:
        roots = referenced_defs(spec, translation)
        defs = call_closure(translation, roots)
        fp = {"defs": defs, "holes_in_footprint": [h for h in holes if h in defs]}
    deps.progress["footprint"] = fp
    hif = fp["holes_in_footprint"]
    if hif:
        log.warning("Footprint reaches %d untranslated hole(s) %s — properties touching "
                    "them are NOT soundly grounded", len(hif), hif)
    else:
        log.info("Footprint: %d translation def(s), no Aeneas holes inside — "
                 "properties soundly grounded", len(fp["defs"]))
    checkpoint.snapshot(deps)
    return fp


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


