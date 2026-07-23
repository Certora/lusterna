"""Aeneas/Lean operations and analysis: translation, build, the `#print axioms` gate, and
spec assembly helpers. The heavy Lean logic, kept out of the trivial file tools in tools.py."""
import logging
import os
import re
import subprocess
from pathlib import Path

from . import checkpoint, tools
from .container import exec_in, OUT_IN
from .schemas import AgentDeps

log = logging.getLogger(__name__)


def analyze_translation(deps: AgentDeps, *, do_commit: bool = True) -> dict:
    """Summarise the Lean translation the TRANSLATE agent produced in `/workspace/out/lean`.

    The agent drives Charon+Aeneas itself (at the shell); this reads whatever landed and
    returns the shape the pipeline already expects for `progress['aeneas']`, so downstream
    stages are unchanged:
      success       — True iff any translation Lean file exists
      lean_files    — generated Lean files (relative to /workspace/out; lakefile excluded)
      lean_path     — the top-level crate module `lean/<Module>.lean`, for downstream tools
      holes         — flat list of untranslated function names (bare `sorry` body)
      holes_by_file — {lean_file: [hole names]} per-file attribution
      commit        — git SHA of the committed lean/ (empty if do_commit=False or empty tree)
    """
    lean_out_dir = f"{OUT_IN}/lean"
    # EXCLUDE `.lake/` — it holds the built lake project's DEPENDENCY source (Aeneas runtime, and,
    # once a stage runs `lake build`/`lake exe cache get`, the entire Mathlib+Qq source tree — 9000+
    # files). Those are not the translation; enumerating and reading them would be pathologically
    # slow (one `cat` per file) AND pollute translation_text/external_axioms/referenced_defs with all
    # of Mathlib. The translation is only lean/<Crate>.lean + lean/<Crate>/**.
    _, lean_list, _ = exec_in(
        deps.container_id,
        ["find", lean_out_dir, "-name", "*.lean", "-not", "-path", "*/.lake/*"], workdir=OUT_IN)
    lean_files = [
        l.strip().removeprefix(OUT_IN + "/")
        for l in lean_list.splitlines()
        if l.strip() and not Path(l.strip()).name.startswith("._")
        and Path(l.strip()).name != "lakefile.lean"   # not a translation module
    ]
    if not lean_files:
        return {"success": False, "lean_files": [], "lean_path": "",
                "holes": [], "holes_by_file": {}, "commit": ""}

    # The crate module is the top-level `lean/<Module>.lean` (submodules live under
    # `lean/<Module>/`). Aeneas CamelCases multi-word crates (`erc20_rs` → `Erc20Rs`).
    top_level = [f for f in lean_files
                 if f.startswith("lean/") and "/" not in f[len("lean/"):]]
    crate_module = top_level[0] if top_level else lean_files[0]

    holes_by_file = _detect_holes(deps, lean_files)
    holes = sorted({h for hs in holes_by_file.values() for h in hs})
    sha = ""
    if do_commit:
        sha = tools.commit(
            deps.container_id,
            f"feat(aeneas): translation ({len(lean_files)} file(s)"
            + (f", {len(holes)} hole(s)" if holes else "") + ")",
            glob="lean/")
    log.info("Translation: %d Lean file(s), %d hole(s), crate module %s",
             len(lean_files), len(holes), crate_module)
    return {
        "success": True,
        "lean_files": lean_files,
        "lean_path": crate_module,
        "holes": holes,
        "holes_by_file": holes_by_file,
        "commit": sha,
    }


def setup_lake(deps: AgentDeps) -> str:
    """Wire a lake project around the agent-produced `lean/` so `lake env lean`/`lake build`
    resolve the bundled Aeneas+Mathlib packages offline. Derives the lib name from the
    top-level generated module. This is build-environment infrastructure (not part of the
    translation): the TRANSLATE agent calls it after running Aeneas so it can compile-check,
    and the harness re-runs it before its own compile gate. Returns a status/ERROR string."""
    lean_out_dir = f"{OUT_IN}/lean"
    _, lean_list, _ = exec_in(deps.container_id,
                              ["find", lean_out_dir, "-maxdepth", "1", "-name", "*.lean"],
                              workdir=OUT_IN)
    mods = [Path(l.strip()).stem for l in lean_list.splitlines()
            if l.strip() and Path(l.strip()).name != "lakefile.lean"]
    if not mods:
        return ("ERROR: no top-level lean/<Module>.lean found — run Aeneas with "
                "`-dest /workspace/out/lean` first")
    lib_name = mods[0]
    _write_lakefile(deps, lean_out_dir, lib_name)
    return f"lake project wired for lib «{lib_name}» (now: lake env lean <file>)"


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


def referenced_defs(spec_text: str, translation_text: str) -> list[str]:
    """Translation def names that *spec_text* mentions by name (comments stripped). Used to
    decide whether a theorem statement references the implementation at all (so `_record_axioms`
    can split established theorems into implementation-verified vs abstract-only lemmas).
    Approximate (token-boundary match)."""
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


def target_holes(translation_text: str, holes: list[str], target_patterns: list[str]) -> list[str]:
    """Holes that are THEMSELVES target functions — a target-pattern `def` left as a bare
    `sorry`, i.e. the target's own body was not translated. The transitive question ("does
    something the target CALLS have a hole?") is answered authoritatively downstream by
    `#print axioms` (a hole is `sorryAx` → the theorem is tainted), so this is deliberately
    the precise, non-transitive signal. Whole-crate (no patterns) ⇒ every hole counts."""
    if not target_patterns:
        return sorted(holes)
    seeds = set(matched_target_defs(translation_text, target_patterns))
    return sorted(h for h in holes if h in seeds)


def external_axioms(translation_text: str) -> list[str]:
    """Top-level `axiom` names in the translation. Aeneas emits `axiom` ONLY for opaque
    external items (auto-opaqued stdlib/crypto, or a dep the agent chose to `--opaque`); the
    crate's own items are `def`/`structure`/`inductive`. These are the target's ASSUMPTIONS —
    a theorem depending on one is tainted by `#print axioms` downstream."""
    return sorted({m.group(1) for m in re.finditer(r"(?m)^axiom\s+([\w.]+)", translation_text)})


def opaqued_targets(translation_text: str, target_patterns: list[str]) -> list[str]:
    """Target-pattern leaves that were emitted as `axiom`s (opaqued) rather than translated —
    i.e. a function UNDER verification was dissolved into an assumption. This must be empty:
    opacity is only ever legitimate for a target's *dependencies*, never the target itself."""
    leaves = {_pattern_leaf(p) for p in target_patterns if _pattern_leaf(p)}
    return sorted({ax.split(".")[-1] for ax in external_axioms(translation_text)
                   if ax.split(".")[-1] in leaves})


def opaque_deps_in_targets(translation_text: str, target_patterns: list[str]) -> list[str]:
    """Opaqued axioms referenced DIRECTLY in the target functions' own bodies — a local,
    one-level signal (NO call-closure) that a target is a thin wrapper over assumed behaviour.
    If the verified properties concern that behaviour, the translation is hollow — the
    `over_opaqued` case. Feeds the TRANSLATE-JUDGE a pointed fact so it need not infer the
    linkage itself; it is a pre-proof QUALITY signal, not a soundness gate (an opaque dependency
    the properties truly need is caught downstream by `#print axioms` tainting the theorem)."""
    axioms = external_axioms(translation_text)
    seeds = set(matched_target_defs(translation_text, target_patterns))
    if not axioms or not seeds:
        return []
    blocks = _def_blocks(translation_text)
    hits = set()
    for name in seeds:
        body = _strip_lean_comments(blocks.get(name, ""))
        hits.update(ax for ax in axioms if _mentions(ax, body))
    return sorted(hits)


def _theorem_names(spec_text: str) -> list[str]:
    """Names as written after `theorem`/`lemma` in the implementation spec."""
    return [m.group(2) for m in re.finditer(r"(?m)^\s*(theorem|lemma)\s+([\w.]+)", spec_text)]


def _theorem_qualified_names(spec_text: str) -> list[str]:
    """Theorem/lemma names PREFIXED with any enclosing `namespace` — parallel to _theorem_names
    (same theorems, same order). `#print axioms` needs the fully-qualified name: a theorem inside
    `namespace Foo` is `Foo.bar`, not `bar`, so querying the bare name fails with 'unknown constant'
    and taints everything. `section` scopes are tracked (they don't contribute to the name but their
    `end` must not pop a namespace)."""
    scopes: list[tuple[bool, str]] = []   # (is_namespace, name), innermost last
    out: list[str] = []
    for raw in spec_text.splitlines():
        s = raw.strip()
        if m := re.match(r"(namespace|section)\s+(\S+)", s):
            scopes.append((m.group(1) == "namespace", m.group(2)))
        elif re.match(r"section\b\s*$", s):        # anonymous section
            scopes.append((False, ""))
        elif re.match(r"end\b", s):
            if scopes:
                scopes.pop()
        elif m := re.match(r"(theorem|lemma)\s+([\w.]+)", s):
            prefix = ".".join(n for is_ns, n in scopes if is_ns)
            out.append(f"{prefix}.{m.group(2)}" if prefix else m.group(2))
    return out


def sorry_bodied_theorems(spec_text: str) -> set[str]:
    """Short names of theorems/lemmas whose proof BODY still contains a literal `sorry` — i.e.
    genuinely-open obligations, as opposed to theorems that compile but are tainted by a
    non-standard axiom (native_decide etc.). Used to split the tainted set for PROVE feedback.
    Each declaration spans from its `theorem`/`lemma` keyword to the next declaration or EOF."""
    decls = list(re.finditer(r"(?m)^\s*(?:theorem|lemma)\s+([\w.]+)", spec_text))
    out: set[str] = set()
    for i, m in enumerate(decls):
        end = decls[i + 1].start() if i + 1 < len(decls) else len(spec_text)
        body = spec_text[m.end():end]
        if re.search(r"\bsorry\b", body):
            out.add(m.group(1).split(".")[-1])
    return out


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
    """Authoritative established-theorem oracle. Ask Lean which impl-spec theorems are GENUINELY
    established — i.e. whose proof term depends on NOTHING beyond the standard trusted axioms
    (propext, Classical.choice, Quot.sound). An untranslated Aeneas hole and an unfinished proof
    BOTH introduce `sorryAx`, and `#print axioms` follows the real proof term through simp sets,
    instances and every definition. It also rejects any OTHER non-standard axiom: `sorryAx`,
    `Lean.ofReduceBool`/`Lean.trustCompiler` (native_decide's compiler trust), and any `axiom` the
    model might smuggle in all taint a theorem — "established" means kernel-checked with only the
    standard axioms.

    Returns {"clean": [names], "tainted": [names], "raw": <trimmed lean output>}, where
    tainted = depends on a non-standard axiom OR could not be resolved (the conservative direction).

    Mechanism: write a throwaway checker that IMPORTS the already-built `Spec.olean` and
    runs `#print axioms` against it, then elaborate just that checker with `lake env lean`.
    The spec is never re-elaborated — no proofs (or `native_decide`) rerun, and imports
    resolve from the compiled artifacts the pipeline already built. The checker is deleted
    afterwards. (Re-elaborating the spec from source instead is fragile: one import or
    proof failure auto-`sorry`s every declaration and taints the whole batch.)
    """
    original = tools.read_out(deps, spec_rel)
    if original.startswith("ERROR:"):
        return {"clean": [], "tainted": [], "raw": original}
    names = _theorem_names(original)
    if not names:
        return {"clean": [], "tainted": [], "raw": ""}
    # Query `#print axioms` by the NAMESPACE-QUALIFIED name (parallel to `names`) — a bare name
    # inside `namespace Foo` is `Unknown constant` and would taint every theorem. Matching the
    # output back to `names` is still by short name, so the returned lists stay short.
    qnames = _theorem_qualified_names(original)

    # spec_rel = lean/<Lib>/Spec.lean  →  spec module <Lib>.Spec (matches the lean_lib root)
    parts = tools._norm_out(spec_rel).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return {"clean": [], "tainted": [], "raw": f"ERROR: unexpected spec path {spec_rel!r}"}
    spec_module = ".".join(parts[1:]).removesuffix(".lean")

    checker_rel = "_axiom_check.lean"          # at out root — outside the lean_lib srcDir
    body = f"import {spec_module}\n\n" + "\n".join(f"#print axioms {n}" for n in qnames) + "\n"
    if (w := tools.write_out(deps, checker_rel, body)).startswith("ERROR:"):
        return {"clean": [], "tainted": [], "raw": w}
    _, out, err = exec_in(deps.container_id,
                          ["timeout", "-k", "10", str(_BUILD_TIMEOUT),
                           "lake", "env", "lean", f"{OUT_IN}/{checker_rel}"],
                          workdir=f"{OUT_IN}/lean", timeout=_BUILD_TIMEOUT + 30)
    exec_in(deps.container_id, ["rm", "-f", f"{OUT_IN}/{checker_rel}"])

    text = f"{out}\n{err}"
    # Each verdict is "'name' does not depend on any axioms" (clean) or "'name' depends on axioms:
    # [a, b, ...]" — clean iff every listed axiom is standard. Lean pretty-prints a long axiom list
    # ACROSS MULTIPLE LINES (one per line), so parse over the whole text with DOTALL rather than
    # line-by-line (a per-line regex misses the closing `]` and mis-taints such theorems).
    verdict: dict[str, bool] = {}
    for m in re.finditer(
            r"'([\w.]+)' (?:(does not depend on any axioms)|depends on axioms: \[(.*?)\])",
            text, re.DOTALL):
        short = m.group(1).split(".")[-1]
        if m.group(2):                  # "does not depend on any axioms"
            verdict[short] = True
        else:
            axes = [a.strip() for a in m.group(3).replace("\n", " ").split(",") if a.strip()]
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


def _write_lakefile(deps: AgentDeps, lean_out_dir: str, lib_name: str) -> None:
    """Generate a lakefile.lean and wire up the pre-resolved package manifest.

    The Docker image contains /opt/lean-template — a minimal lake project that
    already ran `lake update` against the bundled Aeneas runtime.  We copy its
    lake-manifest.json and symlink its .lake/packages so `lake build` works
    fully offline.

    *lib_name* is the ACTUAL generated module name (Aeneas CamelCases multi-word crates,
    e.g. `erc20_rs` → `Erc20Rs`); the lean_lib name/glob must match it exactly or
    `lake build`'s `andSubmodules` glob fails to find the module. The package name is
    cosmetic, so we reuse *lib_name*.
    """
    crate = lib_name      # package name — cosmetic; reuse the lib name

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
    code, out, err = exec_in(
        deps.container_id,
        ["timeout", "-k", "10", str(_BUILD_TIMEOUT), "lake", *args],
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


def theorem_statement(spec_text: str, name: str) -> str:
    """The statement text of theorem *name* — binders + proposition, up to (not including) the
    proof `:=`. Used to decide whether a theorem references the implementation (referenced_defs on
    it). Returns '' if not found."""
    m = re.search(rf"(?m)^theorem\s+{re.escape(name)}\b", spec_text)
    if not m:
        return ""
    tail = spec_text[m.start():]
    # Cut at this theorem's proof `:=` via the bracket-aware splitter, so a nested record-update
    # `:=` survives (a naive find(':=') would truncate the statement mid-expression). If there is
    # no top-level `:=` at all, stop at the blank line so we don't run into the next theorem.
    stmt = _proposition_only(tail)
    return stmt if stmt != tail.rstrip() else tail.split("\n\n", 1)[0]


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


def _proposition_only(signature: str) -> str:
    """The proposition part of a theorem signature, dropping only an accidental trailing proof.

    Splits at the first `:=` that is NOT nested inside (), [], or {} — so Lean statement syntax
    that legitimately contains `:=` (a record update `{ x with f := v }`, a `let … := …`) is
    PRESERVED (depth > 0), while a stray top-level `:= <proof>` the model shouldn't have included
    is stripped. A naive `split(':=')[0]` truncated record-update statements mid-expression."""
    depth = 0
    for i, c in enumerate(signature):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == ":" and signature[i + 1:i + 2] == "=":
            return signature[:i].rstrip()
    return signature.rstrip()


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
