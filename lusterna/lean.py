"""Aeneas/Lean operations and analysis: translation, build, the `#print axioms` gate, and
spec assembly helpers. The heavy Lean logic, kept out of the trivial file tools in tools.py."""
import json
import logging
import os
import re
import subprocess
import tomllib
from pathlib import Path

from . import checkpoint, tools
from .container import exec_in, OUT_IN, REPO_IN, VERIF_IN
from .schemas import AgentDeps

log = logging.getLogger(__name__)


def _container_exec(deps: AgentDeps, cmd_args: list[str]) -> None:
    """Run a provisioning command in the container, raising on failure. For the harness's own
    file wiring (the lakefile, the checks module) — `container.exec_in` is for commands whose
    output or exit code the caller reasons about."""
    subprocess.run(["docker", "exec", deps.container_id] + cmd_args,
                   capture_output=True, text=True, check=True)


def _container_write(deps: AgentDeps, path: str, content: str) -> None:
    """Write *content* to *path* inside the container, creating its parent directory."""
    _container_exec(deps, ["mkdir", "-p", str(Path(path).parent)])
    subprocess.run(["docker", "exec", "--interactive", deps.container_id, "tee", path],
                   input=content, capture_output=True, text=True, check=True)



def analyze_translation(deps: AgentDeps, *, do_commit: bool = True) -> dict:
    """Summarise the Lean translation the TRANSLATE agent produced in `/workspace/out/lean`.

    The agent drives Charon+Aeneas itself (at the shell); this reads whatever landed and
    returns the shape the pipeline already expects for `progress['aeneas']`, so downstream
    stages are unchanged:
      success       — True iff any translation Lean file exists
      error         — a specific reason the tree is unusable (currently: `lean/` is a symlink),
                      empty otherwise. The gate reports it verbatim, because "no Lean was produced"
                      is actively misleading for a tree that exists but cannot be walked.
      lean_files    — generated Lean files (relative to /workspace/out; lakefile excluded)
      lean_path     — the top-level crate module `lean/<Module>.lean`, for downstream tools
      holes         — flat list of untranslated function names (bare `sorry` body)
      holes_by_file — {lean_file: [hole names]} per-file attribution
      commit        — git SHA of the committed lean/ (empty if do_commit=False or empty tree)
    """
    lean_out_dir = f"{OUT_IN}/lean"
    # `lean/` must be a REAL directory. `/workspace/out` is itself a symlink to
    # `/workspace/repo/verification`, so an agent that "reuses" an existing translation by
    # symlinking `lean` at `/workspace/repo/verification/lean` has pointed the directory at ITSELF —
    # and `find` does not descend into a symlinked start point, so the tree reads as empty and the
    # gate blames the agent for producing no Lean while its own `lake env lean` said COMPILE OK.
    # REJECT rather than follow: `find -L` would let the gate pass, and then `tools.commit`'s
    # `git add -A` commits the symlink, delivering a branch with no Lean in it at all — a loud
    # failure traded for a silent one.
    if exec_in(deps.container_id, ["test", "-L", lean_out_dir], workdir=OUT_IN)[0] == 0:
        return {"success": False, "lean_files": [], "lean_path": "", "holes": [],
                "holes_by_file": {}, "commit": "",
                "error": (f"{lean_out_dir} is a SYMLINK, not a directory. /workspace/out already IS "
                          f"/workspace/repo/verification, so linking `lean` under it points the "
                          f"directory at itself. Replace it with a real directory and put the "
                          f"generated Lean inside: `rm -f {lean_out_dir} && mkdir -p {lean_out_dir}`, "
                          f"then re-run aeneas with `-dest {lean_out_dir}`.")}
    # EXCLUDE `.lake/` — it holds the built lake project's DEPENDENCY source (Aeneas runtime, and,
    # once a stage runs `lake build`/`lake exe cache get`, the entire Mathlib+Qq source tree — 9000+
    # files). Those are not the translation; enumerating and reading them would be pathologically
    # slow (one `cat` per file) AND pollute translation_text/external_axioms with all of Mathlib.
    # The translation is only lean/<Crate>.lean + lean/<Crate>/**.
    _, lean_list, _ = exec_in(
        deps.container_id,
        ["find", lean_out_dir, "-name", "*.lean", "-not", "-path", "*/.lake/*"], workdir=OUT_IN)
    lean_files = [
        l.strip().removeprefix(OUT_IN + "/")
        for l in lean_list.splitlines()
        if l.strip() and not Path(l.strip()).name.startswith("._")
        and Path(l.strip()).name not in _HARNESS_OWNED_LEAN
    ]
    if not lean_files:
        return {"success": False, "lean_files": [], "lean_path": "",
                "holes": [], "holes_by_file": {}, "commit": "", "error": ""}

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
            + (f", {len(holes)} hole(s)" if holes else "") + ")")
    log.info("Translation: %d Lean file(s), %d hole(s), crate module %s",
             len(lean_files), len(holes), crate_module)
    return {
        "success": True,
        "error": "",
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
    _write_lint_tool(deps, lean_out_dir, lib_name)
    return f"lake project wired for lib «{lib_name}» (now: lake env lean <file>)"


# Files the HARNESS writes into `lean/`. They are build/tooling infrastructure, not the agent's
# translation, so `analyze_translation` filters them out — otherwise they would be reported to
# TRANSLATE-JUDGE as generated Lean and concatenated into `translation_text`, where their `def`s
# would enter `_def_blocks` (hence `target_defs`, the hole scan).
_LINT_TOOL_NAME = "LusternaChecks.lean"
# The SCHEMA ATTRIBUTES live in their own file, and the split is load-bearing: a spec module
# `import`s this one to write `@[lusterna]`, which makes its attribute names a stable public
# API, while prior campaigns' spec modules are immutable (`pipeline._restore_prior_specs`). Keeping
# the churn-prone checker logic in LusternaChecks means it can be rewritten freely without risking
# an old spec module's build. See the append-only rule in spec_schemas.lean's own header.
_SCHEMA_TOOL_NAME = "LusternaSchemas.lean"
_HARNESS_OWNED_LEAN = {"lakefile.lean", _LINT_TOOL_NAME, _SCHEMA_TOOL_NAME}

_LINT_TOOL_SRC = (Path(__file__).parent / "checks" / "spec_checks.lean").read_text()
_SCHEMA_TOOL_SRC = (Path(__file__).parent / "checks" / "spec_schemas.lean").read_text()


def _write_lint_tool(deps: AgentDeps, lean_out_dir: str, lib_name: str) -> None:
    """Copy the harness-owned mechanical-checks module (`checks/spec_checks.lean`) into this
    crate's own lean tree, at `lean/<lib_name>/LusternaChecks.lean` — module
    `<lib_name>.LusternaChecks`. It only depends on Aeneas (crate-agnostic), so it compiles
    unchanged for every crate; placing it under the crate's own lib means `lake build` picks it
    up automatically via the existing `.andSubmodules` glob, no separate build step. The flip side
    of that convenience: the harness's own compile gate now typechecks it, so a Lean/Aeneas
    metaprogramming-API drift surfaces as a build failure — tests/checklean/verify.py is what
    catches that before a campaign does.

    Writes TWO files. `LusternaSchemas.lean` registers the two family attributes `@[lusterna]` /
    `@[lusterna_lemma]` and is what a SPEC MODULE imports; `LusternaChecks.lean` imports it and holds
    the checks.

    These are a GATE, not a tool: `check_spec_gate` runs `checkSpecGate` over the campaign's theorems
    and a finding rejects FORMALISE with a critique naming the check and the rule. Nothing here is
    interpreted by an agent. That is only sound because each theorem DECLARES its schema, which is
    what retires the shape rules' documented false positives — see `checkSpecGate` in
    spec_checks.lean. HARNESS-OWNED like the lakefile: an agent must not edit or delete either file,
    and (like the lakefile) both are rewritten on every `setup_lake`.
    """
    _container_write(deps, f"{lean_out_dir}/{lib_name}/{_SCHEMA_TOOL_NAME}", _SCHEMA_TOOL_SRC)
    # The checker imports the schemas module, whose path is crate-specific (the lakefile globs
    # `.andSubmodules <lib_name>`, so a root-level module would never be built). Substituted here
    # rather than hardcoded, exactly as `_write_lakefile` interpolates the same name.
    checks = _LINT_TOOL_SRC.replace("LUSTERNA_CRATE", lib_name)
    _container_write(deps, f"{lean_out_dir}/{lib_name}/{_LINT_TOOL_NAME}", checks)


# Attributes and declaration modifiers that may precede a keyword, in any number and either order.
# Every declaration matcher in this module goes through it: requiring the keyword to be the first
# token on its line made `@[progress] theorem …` and `private theorem …` invisible to all of them
# at once, and Aeneas itself emits `@[global_simps, irreducible] def <Crate>.CONST : … := …`.
_DECL_PREFIX = (r"(?:@\[[^\]]*\]\s*|(?:private|protected|nonrec|scoped|partial|unsafe|noncomputable)\s+)*")

_DEF_DECL_RE = re.compile(r"(?m)^" + _DECL_PREFIX + r"def\s+([\w.]+)")
_DEF_BLOCK_END_RE = re.compile(r"(?m)^" + _DECL_PREFIX + r"(?:def|end)\b")


def _def_blocks(text: str) -> dict[str, str]:
    """Map each `def NAME` to its source block (header through just before the next
    top-level `def`/`end`). Both the finder and the terminator carry `_DECL_PREFIX`, so an
    attributed def is found AND closes the previous block instead of being swallowed into it."""
    blocks: dict[str, str] = {}
    for m in _DEF_DECL_RE.finditer(text):
        rest = text[m.end():]
        nxt = _DEF_BLOCK_END_RE.search(rest)
        end = m.end() + (nxt.start() if nxt else len(rest))
        blocks[m.group(1)] = text[m.start():end].rstrip()
    return blocks


def external_axioms(translation_text: str) -> list[str]:
    """Top-level `axiom` names in the translation. Aeneas emits `axiom` ONLY for opaque
    external items (auto-opaqued stdlib/crypto, or a dep the agent chose to `--opaque`); the
    crate's own items are `def`/`structure`/`inductive`. These are the target's ASSUMPTIONS —
    a theorem depending on one is tainted by `#print axioms` downstream."""
    return sorted({m.group(1) for m in re.finditer(r"(?m)^axiom\s+([\w.]+)", translation_text)})


# `[ \t]*`, not `\s*`: `\s` crosses newlines, which would start a match on a blank line above
# the declaration. `_DECL_PREFIX` still spans an attribute written on its own line.
_THEOREM_DECL_RE = re.compile(r"(?m)^[ \t]*" + _DECL_PREFIX + r"(?:theorem|lemma)\s+([\w.]+)")


def _theorem_names(spec_text: str) -> list[str]:
    """Names as written after `theorem`/`lemma` in the implementation spec. Kept PARALLEL to
    `_theorem_qualified_names` (same theorems, same order): `check_axioms` zips them."""
    return [m.group(1) for m in _THEOREM_DECL_RE.finditer(spec_text)]


def _qualified_decls(spec_text: str, kind_re: str) -> list[tuple[str, str]]:
    """Single namespace-aware pass: for each top-level declaration whose keyword matches *kind_re*
    (e.g. r'axiom' or r'(?:theorem|lemma)' — must be NON-capturing), yield (fully-qualified name,
    source block). The qualified name is what `#print axioms` reports — a decl inside `namespace Foo` is `Foo.bar`, not
    `bar`, so the bare name would 'unknown constant' and taint everything — and the block runs from
    the decl line to just before the next top-level construct. One walker keeps name and body
    intrinsically paired (no positional zip to drift) and shares the namespace/`section`/`end` scope
    tracking across callers. `section` scopes are tracked (their `end` must not pop a namespace) but
    do not contribute to the name.

    LINE-BASED, unlike `_THEOREM_DECL_RE`, which matches across newlines. The two must stay in step
    — `check_axioms` zips `_theorem_names` against `_theorem_qualified_names` — and they do for
    every form the stages actually write (attribute on its own line, attribute inline, modifiers
    stacked). The one shape that would desync them is a MULTI-LINE attribute whose closing bracket
    shares a line with the keyword (`@[foo\n  bar] theorem baz`): the regex finds it, this walker's
    per-line gate does not."""
    scopes: list[tuple[bool, str]] = []      # (is_namespace, name), innermost last
    lines = spec_text.splitlines()
    # *kind_re* is documented as non-capturing, so group(1) stays the declaration's name.
    kind = re.compile(_DECL_PREFIX + rf"{kind_re}\s+([\w.]+)")
    out: list[tuple[str, str]] = []
    cur: tuple[str, int] | None = None       # (qualified_name, start line idx) of the open decl
    def flush(end: int) -> None:
        nonlocal cur
        if cur:
            out.append((cur[0], "\n".join(lines[cur[1]:end])))
            cur = None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not re.match(_DECL_PREFIX + r"(axiom|def|theorem|lemma|namespace|section|end)\b", s):
            continue
        flush(i)                             # any top-level construct closes the open block
        if m := re.match(r"(namespace|section)\s+(\S+)", s):
            scopes.append((m.group(1) == "namespace", m.group(2)))
        elif re.match(r"section\b\s*$", s):  # anonymous section
            scopes.append((False, ""))
        elif re.match(r"end\b", s):
            if scopes:
                scopes.pop()
        elif m := kind.match(s):
            prefix = ".".join(n for is_ns, n in scopes if is_ns)
            cur = (f"{prefix}.{m.group(1)}" if prefix else m.group(1), i)
    flush(len(lines))
    return out


def _theorem_qualified_names(spec_text: str) -> list[str]:
    """Theorem/lemma names prefixed with any enclosing `namespace` — parallel to `_theorem_names`
    (same theorems, same order), the fully-qualified names `#print axioms` needs."""
    return [q for q, _ in _qualified_decls(spec_text, r"(?:theorem|lemma)")]


def _qualified_axiom_names(spec_text: str) -> list[str]:
    """`axiom` names prefixed with any enclosing `namespace` — the qualified names `#print axioms`
    reports in a theorem's dependency list, so a declared trusted assumption can be matched to it."""
    return [q for q, _ in _qualified_decls(spec_text, r"axiom")]





def _run_lean_checker(deps: AgentDeps, body: str, rel: str) -> tuple[int, str]:
    """Elaborate a THROWAWAY top-level Lean file (BODY) with `lake env lean`, against the already-built
    oleans, then delete it — returning (lean exit code, combined stdout+stderr). The shared mechanism
    behind both the `#print axioms` gate and the refutation type-tie: neither re-elaborates the spec
    from source (which would auto-`sorry` and taint the whole batch on one import/proof failure). A
    write failure returns (1, "ERROR: …") — distinguishable from Lean output, which never starts with
    an uppercase `ERROR:`."""
    if (w := tools.write_out(deps, rel, body)).startswith("ERROR:"):
        return 1, w
    code, out, err = exec_in(deps.container_id,
                             ["timeout", "-k", "10", str(_BUILD_TIMEOUT),
                              "lake", "env", "lean", f"{OUT_IN}/{rel}"],
                             workdir=f"{OUT_IN}/lean", timeout=_BUILD_TIMEOUT + 30)
    exec_in(deps.container_id, ["rm", "-f", f"{OUT_IN}/{rel}"])
    return code, f"{out}\n{err}"


def _empty_axioms(raw: str = "") -> dict:
    """The empty check_axioms result — the canonical 3-way shape every early return and every caller
    relies on (nothing clean/assumed/tainted). Keeping it in one place stops an early return from
    silently omitting a key (e.g. `assumed`) that downstream code indexes."""
    return {"clean": [], "assumed": {}, "tainted": [], "sorry": [], "raw": raw}


def check_axioms(deps: AgentDeps, spec_rel: str) -> dict:
    """Authoritative established-theorem oracle. Ask Lean which impl-spec theorems are GENUINELY
    established — i.e. whose proof term depends on NOTHING beyond the standard trusted axioms
    (propext, Classical.choice, Quot.sound). An untranslated Aeneas hole and an unfinished proof
    BOTH introduce `sorryAx`, and `#print axioms` follows the real proof term through simp sets,
    instances and every definition. It also rejects any OTHER non-standard axiom: `sorryAx`,
    `Lean.ofReduceBool`/`Lean.trustCompiler` (native_decide's compiler trust), and any `axiom` the
    model might smuggle in all taint a theorem — "established" means kernel-checked with only the
    standard axioms.

    Returns {"clean": [names], "assumed": {name: [assumption_qnames]}, "tainted": [names],
    "raw": <trimmed lean output>}. A THREE-way partition (the gate does not relax — it partitions):
      • clean   — depends only on the standard trusted axioms;
      • assumed — depends only on standard axioms + DECLARED trusted assumptions (the `axiom`s in the
        assumptions module, admitted by legitimacy_check); records which assumptions it leans on;
      • tainted — anything else (`sorryAx`, native_decide compiler trust, an UNdeclared axiom) OR
        could not be resolved (the conservative direction). `sorryAx`/native_decide can never be
        `assumed`. With no assumptions module present, `assumed` is empty and this reduces exactly to
        the prior binary clean/tainted gate.

    Mechanism (MetaM): a throwaway driver IMPORTS the already-built `Spec.olean` + the checker and
    calls `checkAxioms`, which runs `Lean.collectAxioms` — the EXACT function `#print axioms` calls —
    over each theorem and classifies it in Lean, emitting one record per theorem. The spec is never
    re-elaborated (no proofs or `native_decide` rerun), and there is no text-parse layer: the verdict
    is the kernel's own axiom set, not a parse of its pretty-printed output. A theorem the driver
    never reaches leaves no record and is conservatively tainted (unresolved).
    """
    original = tools.read_out(deps, spec_rel)
    if original.startswith("ERROR:"):
        return _empty_axioms(original)
    names = _theorem_names(original)
    if not names:
        return _empty_axioms()
    # Query by the NAMESPACE-QUALIFIED name (parallel to `names`) — the constant name `collectAxioms`
    # needs; a bare name inside `namespace Foo` would not resolve.
    qnames = _theorem_qualified_names(original)

    # spec_rel = lean/<Lib>/Spec.lean  →  spec module <Lib>.Spec (matches the lean_lib root)
    parts = tools._norm_out(spec_rel).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return _empty_axioms(f"ERROR: unexpected spec path {spec_rel!r}")
    lib, spec_module = parts[1], ".".join(parts[1:]).removesuffix(".lean")

    declared = declared_assumptions(deps)   # the trusted base — declared `axiom`s in the module
    qlist = ", ".join("`" + q for q in qnames)
    dlist = ", ".join("`" + d for d in sorted(declared))
    body = (f"import {spec_module}\nimport {lib}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            f"  checkAxioms #[{qlist}] #[{dlist}]\n"
            f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_axiom_check.lean")
    if text.startswith("ERROR:") or not _driver_ran(text):
        # The driver died — nothing was established. Conservative + loud: every theorem is tainted
        # (unresolved), never silently reported clean.
        log.warning("check_axioms: driver did not finish (rc=%s) — every theorem conservatively "
                    "tainted. Lean output tail:\n%s", code, text[-1200:])
        return {"clean": [], "assumed": {}, "tainted": list(names), "sorry": list(names),
                "raw": text[-3000:]}
    # Records keyed by the qualified name the checker classified; a qname with no record is UNRESOLVED
    # (the driver never reached it) — conservatively tainted.
    recs = {r.get("theorem"): r for r in _parse_check_records(text) if r.get("check") == "axioms"}
    clean, assumed, tainted, unresolved, sorry = [], {}, [], [], []
    for written, qual in zip(names, qnames):    # parallel lists, same order
        r = recs.get(qual)
        if r is None:                            # the checker never classified this one
            tainted.append(written)
            unresolved.append(written)
            continue
        status = r.get("status")
        if status == "clean":
            clean.append(written)
        elif status == "assumed":                # rests only on declared trusted assumptions
            assumed[written] = sorted(r.get("used", []))
        else:                                    # tainted: sorryAx / native_decide / undeclared axiom
            tainted.append(written)
            # An OPEN obligation is a theorem still resting on `sorryAx` (a literal `sorry` or an
            # untranslated Aeneas hole) — the collectAxioms replacement for the old `sorry`-text scan.
            if "sorryAx" in r.get("used", []):
                sorry.append(written)
    if unresolved:
        # Not a soundness signal — the checker couldn't read these back. Surface it loudly
        # instead of silently reporting them as tainted.
        log.warning("check_axioms: %d/%d theorem(s) UNRESOLVED (conservatively tainted, not a "
                    "real axiom finding): %s — lean output tail:\n%s",
                    len(unresolved), len(names), unresolved, text[-1200:])
    log.info("check_axioms: %d clean, %d assumed (modulo %d declared), %d tainted, of %d theorem(s)",
             len(clean), len(assumed), len(declared), len(tainted), len(names))
    return {"clean": clean, "assumed": assumed, "tainted": tainted, "sorry": sorry,
            "raw": text[-3000:]}


def impl_references(deps: AgentDeps, spec_rel: str) -> dict[str, bool]:
    """Which of a spec module's theorems VERIFY THE IMPLEMENTATION — i.e. their STATEMENT references a
    `def` from the Aeneas TRANSLATION (a real translated def), vs a purely abstract helper lemma over
    the spec's own predicates + trusted libraries. Returns `{written_theorem_name: bool}`.

    Decided in the built environment by `checkImplReference` (`getUsedConstants` on each theorem's
    elaborated TYPE, matched to the translation's own modules via `getModuleFor?`) — robust to
    `open`/namespacing, unlike the former text scan, which matched the full in-namespace def name
    (`crate.foo.Bar.measure`) against a statement that referenced it by its opened short name
    (`Bar.measure`) and so wrongly reported every theorem abstract-only. A
    theorem the driver never reaches is conservatively False (abstract), never a false impl-verified."""
    original = tools.read_out(deps, spec_rel)
    if original.startswith("ERROR:"):
        return {}
    names = _theorem_names(original)
    if not names:
        return {}
    qnames = _theorem_qualified_names(original)
    parts = tools._norm_out(spec_rel).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return {n: False for n in names}
    lib, spec_module = parts[1], ".".join(parts[1:]).removesuffix(".lean")
    # The modules a translated `def` lives in — the Aeneas output files recorded at TRANSLATE.
    trans_mods = sorted({m for f in deps.progress.get("aeneas", {}).get("lean_files", [])
                         for m in [_module_of(f)] if m})
    if not trans_mods:
        return {n: False for n in names}
    qlist = ", ".join("`" + q for q in qnames)
    mlist = ", ".join("`" + m for m in trans_mods)
    body = (f"import {spec_module}\nimport {lib}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            f"  checkImplReference #[{qlist}] #[{mlist}]\n"
            f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_impl_ref.lean")
    if text.startswith("ERROR:") or not _driver_ran(text):
        log.warning("impl_references: driver did not finish (rc=%s) — treating every theorem as "
                    "abstract (conservative, never a false impl-verified). Lean tail:\n%s", code,
                    text[-800:])
        return {n: False for n in names}
    recs = {r.get("theorem"): bool(r.get("refs_impl"))
            for r in _parse_check_records(text) if r.get("check") == "impl_ref"}
    return {written: recs.get(qual, False) for written, qual in zip(names, qnames)}


def _axiom_fix_hint(ax: str) -> str:
    """The category of an opaque axiom a target reached, and how to remove it. `collectAxioms` reports
    the qualified name, so we match on substrings of it. Domain-agnostic: it names Rust/Lean surface
    kinds (formatting, serialization, native_decide), never a target's own types."""
    a = ax.lower()
    if "native.decide" in a or "reducebool" in a or "trustcompiler" in a:
        return ("native_decide compiler-trust — a modelled op/helper used `native_decide`/`decide` on "
                "a non-trivial term; replace it with a real proof or a plain `def` so no compiler-trust "
                "axiom remains")
    if "sorryax" in a:
        return "a `sorry` hole — the target reaches an un-translated body; translate it fully"
    if any(k in a for k in ("fmt", "display", "debug", "tostring")):
        return ("a formatting/`Display`/`Debug` surface — DROP it (exclude `core::fmt`) or give the "
                "modelled type a self-contained body; NEVER delegate a modelled type's Display back to "
                "the original")
    if any(k in a for k in ("serialize", "deserialize", "serde", "borsh", "fromstr")):
        return ("a serialization/parsing surface — DROP it; it is irrelevant to the value the "
                "properties constrain")
    return ("an opaque type/primitive — if a property constrains its VALUE, MODEL it concretely; "
            "otherwise PROJECT it out of the target-reachable struct (drop the field — a Pubkey, "
            "account key, or padding no property reads)")


def _summarize_footprint(axioms: list[str]) -> str:
    """One compact line describing a target's opaque axioms, grouped by fix-category so a target that
    reaches 100 `…native.decide.ax_N` axioms reads as one line, not 100."""
    groups: dict[str, list[str]] = {}
    for ax in axioms:
        groups.setdefault(_axiom_fix_hint(ax), []).append(ax)
    parts = []
    for hint, axs in groups.items():
        more = f" (+{len(axs) - 1} more)" if len(axs) > 1 else ""
        parts.append(f"`{axs[0]}`{more} — {hint}")
    return "; ".join(parts)


def target_footprint_gate(deps: AgentDeps, translation: str, lean_files: list[str],
                          lean_path: str) -> dict:
    """THE TAINT GATE — a HARD, FAIL-CLOSED TRANSLATE gate. No target function may transitively rest on
    an opaque axiom, because `collectAxioms` closes over its whole body: one opaque axiom in a target's
    footprint TAINTS every theorem about it (the exact `#print axioms` verdict, moved from PROVE to
    TRANSLATE so a doomed translation is blocked in seconds, not after a $100 PROVE). A clean
    translation — the sanctioned design — has an EMPTY target footprint: the substrate is MODELLED as
    real defs and irrelevant surface is DROPPED, so nothing opaque is reachable from a goal.

    Returns `{"ok": bool, "footprint": {def: [axioms]}, "feedback": str}`. `ok` is True ONLY when the
    check RAN and every target's footprint is empty. It is FALSE — blocking — when any target reaches an
    opaque axiom (feedback categorises each leak and its fix) OR the check could not run (build/driver
    failure, or ZERO targets matched — the false-clean a name mismatch produces). Fail-closed: silence
    is never 'clean'. This is a soundness gate; the semantic 'is this surface even relevant' call is the
    TRANSLATE-JUDGE's (`irrelevant_surface`), against INFER's declared relevant-state.

    Pass INFER's target patterns as dotted-SUFFIX forms; `checkDefAxioms` DISCOVERS the matching crate
    defs in the built environment (via `nameHasSuffix`, like the legitimacy/schema checks), so the
    harness never reconstructs a namespace-qualified constant name in Python."""
    patterns = _target_name_forms(deps.progress.get("target_patterns", []))
    modules = [m for m in (_module_of(f) for f in lean_files) if m]
    lib = _module_of(lean_path)
    if not patterns:
        # Whole-crate mode (no explicit targets): nothing to point the gate at. Not a soundness hole —
        # `#print axioms` at PROVE still gates every theorem — so do not block here.
        log.info("target_footprint_gate: no target patterns (whole-crate mode) — gate skipped; "
                 "`#print axioms` at PROVE remains authoritative")
        return {"ok": True, "footprint": {}, "feedback": ""}
    if not modules or not lib:
        return {"ok": False, "footprint": {},
                "feedback": "the harness could not locate the translated modules to verify the target "
                            "axiom footprint — ensure the translation is a single top-level lean/<Crate>.lean."}
    if not build(deps).get("success"):
        return {"ok": False, "footprint": {},
                "feedback": "the harness could not verify the target axiom footprint because the "
                            "project did not `lake build`. Fix the build so the taint check can run."}
    imports = "\n".join(f"import {m}" for m in modules)
    plist = ", ".join("`" + p for p in patterns)
    body = (f"{imports}\nimport {lib}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "set_option maxRecDepth 8000 in\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            f"  checkDefAxioms #[{plist}]\n"
            f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_def_axioms.lean")
    if text.startswith("ERROR:") or not _driver_ran(text):
        log.warning("target_footprint_gate: driver did not finish (rc=%s) — BLOCKING (fail-closed). "
                    "Lean tail:\n%s", code, text[-800:])
        return {"ok": False, "footprint": {},
                "feedback": "the harness could not verify the target axiom footprint (the checker "
                            "driver did not finish). This blocks fail-closed; the translation must "
                            "build cleanly so the taint check can run."}
    recs = {r.get("def"): sorted(r.get("opaque"))
            for r in _parse_check_records(text) if r.get("check") == "def_axioms"}
    # ZERO matched defs while there ARE target patterns is the catastrophic false-clean — the check
    # analysed nothing. BLOCK (never read as 'footprint empty ⇒ clean'): a name/namespace mismatch, or
    # every target left un-translated.
    if not recs:
        log.warning("target_footprint_gate: matched NO target def — VACUOUS, BLOCKING (fail-closed). "
                    "patterns=%s", patterns)
        return {"ok": False, "footprint": {},
                "feedback": (f"the taint check matched NO target function in the built translation "
                             f"(patterns {patterns}). Every target must be a real translated `def` — a "
                             f"name mismatch or an un-translated target makes the check vacuous, which "
                             f"blocks fail-closed.")}
    foot = {d: ax for d, ax in recs.items() if ax}
    if not foot:
        log.info("target_footprint_gate: %d target(s) clean — empty opaque footprint", len(recs))
        return {"ok": True, "footprint": {}, "feedback": ""}
    log.warning("TRANSLATE: ⚑ TAINT GATE BLOCKED — %d target def(s) rest on OPAQUE axioms: %s",
                len(foot), foot)
    lines = [f"  - `{d}` reaches {_summarize_footprint(ax)}" for d, ax in sorted(foot.items())]
    feedback = (
        f"TAINT GATE: {len(foot)} target function(s) transitively rest on an OPAQUE axiom. Axiom taint "
        "is TRANSITIVE over a function's whole body (error/panic/formatting branches included), so each "
        "of these will taint EVERY theorem about it at PROVE — a verified target must reach ONLY Lean's "
        "standard axioms (propext, Classical.choice, Quot.sound). This is irrelevant surface that leaked "
        "into the verified core. Remove each so the target's footprint is EMPTY (drop the surface, or "
        "model the value concretely — never delegate a modelled type's Display/serde back to the "
        "original):\n" + "\n".join(lines))
    return {"ok": False, "footprint": foot, "feedback": feedback}


_LLBC_CHECKED_OP_RE = "(panic|checked)\\.[-+*]"   # a plain operator compiled with overflow-checks ON


_OVERFLOW_KEYS = ("overflow-checks", "debug-assertions")


def _overflow_subset(rel: dict) -> dict:
    """The overflow-relevant slice of a `[profile.release]` table — the two overflow flags at the top
    level and per package, NORMALISED (absent ⇒ False, the Rust release default) so two profiles
    compare by intent: `{overflow-checks, debug-assertions, package: {name: {...}}}`. Non-overflow
    keys (lto, opt-level, …) are ignored: they do not affect whether an op panics."""
    def flags(t: dict) -> dict:
        return {k: bool(t.get(k, False)) for k in _OVERFLOW_KEYS}
    out = flags(rel)
    out["package"] = {name: flags(t) for name, t in (rel.get("package") or {}).items()
                      if isinstance(t, dict) and any(k in t for k in _OVERFLOW_KEYS)}
    return out


def _any_checked(prof: dict) -> bool:
    """Does this overflow profile enable checking ANYWHERE — top level or any package?"""
    return (any(prof.get(k) for k in _OVERFLOW_KEYS)
            or any(any(p.values()) for p in (prof.get("package") or {}).values()))


def _release_profile(deps: AgentDeps, manifest: str) -> dict | None:
    """The `[profile.release]` table of *manifest* (empty dict if the manifest parses but has none);
    None if it cannot be read/parsed."""
    c, text, _ = exec_in(deps.container_id, ["cat", manifest])
    if c != 0:
        return None
    try:
        return ((tomllib.loads(text).get("profile") or {}).get("release")) or {}
    except tomllib.TOMLDecodeError:
        return None


def _deploy_overflow_profile(deps: AgentDeps) -> tuple[dict, str]:
    """The DEPLOYED build's overflow profile (ground truth): the overflow subset of the target's
    workspace-root `[profile.release]`, EXCLUDING the generated extraction crate under verification/.
    `release` is the deployed profile — certain on Solana (`cargo build-sbf` builds release), standard
    for any shipped program. Absent ⇒ the Rust release default (all-wrapping)."""
    _, out, _ = exec_in(deps.container_id, ["sh", "-c",
        f"find {REPO_IN} -maxdepth 4 -name Cargo.toml -not -path '*/verification/*' "
        f"-not -path '*/target/*' 2>/dev/null"])
    for m in (l.strip() for l in out.splitlines() if l.strip()):
        rel = _release_profile(deps, m)
        if rel:                               # a manifest that actually carries [profile.release]
            return _overflow_subset(rel), m
    return _overflow_subset({}), "(no [profile.release] in the target — release default = wrap)"


def _extraction_overflow_profile(deps: AgentDeps) -> tuple[dict | None, str]:
    """The overflow profile of the crate Charon compiled, WHEN it is a generated extraction crate under
    verification/ (its own workspace root, so its `[profile.release]` governs the compile). None ⇒ no
    extraction crate found — an in-place translation of the real crate, whose profile IS the
    deployment's, so it matches by construction."""
    _, out, _ = exec_in(deps.container_id, ["sh", "-c",
        f"find {VERIF_IN}/translate -name Cargo.toml -not -path '*/target/*' 2>/dev/null"])
    for m in (l.strip() for l in out.splitlines() if l.strip()):
        rel = _release_profile(deps, m)
        if rel is not None:                   # an extraction manifest (its [profile.release] may be {})
            return _overflow_subset(rel), m
    return None, "(no extraction crate — in-place translation)"


def _model_has_checked_ops(deps: AgentDeps) -> tuple[bool | None, str]:
    """Whether the emitted `.llbc` compiled plain arithmetic OPERATORS checked — Charon's own
    OverflowMode, read back from its rendering. `charon cargo --preset=aeneas` resugars a checked op to
    `panic.+` (OverflowMode::Panic); plain `charon cargo` shows the pre-resugar `checked.+`; either is a
    checked operator, `wrap.+` is not, and a source `wrapping_add` is a call (never `panic.`/`checked.`
    on an operator), so this reads the profile's effect, not intentional wrapping. True | False | None
    (no `.llbc` to read)."""
    _, out, _ = exec_in(deps.container_id, ["sh", "-c",
        f"find {REPO_IN} -name '*.llbc' -not -path '*/target/*' 2>/dev/null"])
    llbcs = [l.strip() for l in out.splitlines() if l.strip()]
    if not llbcs:
        return None, "no .llbc found to confirm the model's overflow posture"
    for f in llbcs:
        c, hit, _ = exec_in(deps.container_id, ["sh", "-c",
            f"charon pretty-print {f} 2>/dev/null | grep -m1 -oE '{_LLBC_CHECKED_OP_RE}'"])
        if c == 0 and hit.strip():
            return True, f"{f}: emits `checked.` operator arithmetic"
    return False, "no `checked.` operator arithmetic in the emitted .llbc"


def check_overflow_posture(deps: AgentDeps) -> dict:
    """TRANSLATE-time VERIFY gate for the overflow-posture mandate (docs/prose/aeneas-fallible-ops.md).
    The model's arithmetic must reproduce the DEPLOYED build's overflow posture. Rather than re-derive
    that posture (Charon cannot compile the uncompilable original — the reason extraction exists), we
    VERIFY the agent compiled under it, against ground truth, in two mechanical steps:

      1. PROFILE MATCH — the extraction crate must carry the deployment's overflow `[profile.release]`
         (the agent copies it verbatim; in-place it is the same manifest and matches by construction).
         A mismatch blocks with the exact profile to copy. This is the regime-1 enforcement, and it
         covers a shared value-type crate too: the copied per-package override applies to the same crate.
      2. `--release` TOOK EFFECT — from the `.llbc`: a wrapping deployment (checks nowhere) whose model
         still emits `checked.` operators means the dev default leaked in (the profile was ignored) → block.

    The agent keeps driving the toolchain; the gate guides it back with ground truth when the profile
    is wrong. A rung-3 HAND-MODELLED value-type has no crate to carry a profile — that its model captures
    the original's overflow behaviour is a fidelity matter, briefed to the agent and checked by REVIEW,
    not here (declared boundary)."""
    deploy_prof, deploy_m = _deploy_overflow_profile(deps)
    comp_prof, comp_m = _extraction_overflow_profile(deps)
    model_checked, model_detail = _model_has_checked_ops(deps)
    deploy_any = _any_checked(deploy_prof)

    profile_mismatch = comp_prof is not None and comp_prof != deploy_prof
    # dev-default leak: a wrapping deployment whose model still checks (or we cannot rule it out).
    release_not_honoured = (not deploy_any) and (model_checked is True or model_checked is None)
    block = profile_mismatch or release_not_honoured

    rec = {"deploy_profile": deploy_prof, "deploy_source": deploy_m,
           "compiled_profile": comp_prof, "compiled_source": comp_m,
           "model_checked_ops": model_checked, "block": block}
    if profile_mismatch:
        rec["feedback"] = (
            "OVERFLOW-PROFILE MISMATCH (soundness): the extraction crate's `[profile.release]` does not "
            "match the deployment's, so the model may not reproduce the shipped overflow behaviour. "
            f"Deployment ({deploy_m}): {deploy_prof}. Extraction ({comp_m}): {comp_prof}. Copy the "
            "deployment's `[profile.release]` overflow keys (overflow-checks and any per-package "
            "debug-assertions/overflow-checks) verbatim into the extraction crate's Cargo.toml and "
            "rebuild `--release`, per the fallible-arithmetic reference.")
    elif release_not_honoured:
        rec["feedback"] = (
            "OVERFLOW POSTURE (soundness): the deployment WRAPS on overflow (no overflow-checks in "
            f"`[profile.release]`: {deploy_prof}), but the model may compile arithmetic CHECKED "
            f"({model_detail}) — the dev-default profile leaked in. Rebuild the crate Charon reads with "
            "`--release` so plain operators wrap as they do in the shipped binary. If the `.llbc` could "
            "not be read, keep it on disk so the posture can be confirmed.")
    log.info("check_overflow_posture: deploy_any=%s profile_mismatch=%s model_checked=%s block=%s",
             deploy_any, profile_mismatch, model_checked, block)
    return rec


# The `LUSTERNA_CHECK {...}` line a check emits. Lean prefixes `#eval` output with a
# `file:line:col: info:` header, so both are optional — the same shape tests/checklean/verify.py
# parses, deliberately, so the harness reads exactly what an agent reads.
_CHECK_LINE_RE = re.compile(
    r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK\s+(\{.*\})\s*$")

# A SKIPPED record means one theorem went unanalysed while the finding list still looks complete —
# the ambiguity the token exists to expose.
_SKIP_LINE_RE = re.compile(
    r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK_SKIPPED\s+(\{.*\})\s*$")

# Every check whose finding BLOCKS the stage. `assumed_postcondition` is in here because the schema
# annotation retires its documented false positives (see `checkSpecGate` in spec_checks.lean); a
# finding on a conforming theorem is a defect, not a prompt. (Failure-strictness is no longer a
# separate check — `schema_conformance`'s `claimFailSafe?` folds it in.)
_GATE_CHECKS = {"schema_conformance", "assumed_postcondition"}

_GATE_DONE = "LUSTERNA_GATE_DONE"


# ── ONE record protocol, ONE parser ──────────────────────────────────────────────────────────────
# Every MetaM check emits `LUSTERNA_CHECK {json}` / `LUSTERNA_CHECK_SKIPPED {json}` and the driver
# prints the `_GATE_DONE` sentinel last. These three helpers are the SOLE reader every gate uses, so
# the fail-closed convention lives in one place: a driver that did not reach the sentinel established
# NOTHING, which is never "clean".
def _driver_ran(text: str) -> bool:
    """The driver reached its end (printed `_GATE_DONE`). `False` means it died mid-run — the
    conservative, blocking case: silence without the sentinel is "could not check", never "clean"."""
    return _GATE_DONE in text


def _parse_check_records(text: str) -> list[dict]:
    """Every `LUSTERNA_CHECK {json}` record the driver emitted. A line that will not parse is dropped
    — a record the harness cannot read is not a finding it can act on (and `_driver_ran` already
    guards the case where the driver produced nothing at all)."""
    out: list[dict] = []
    for m in _CHECK_LINE_RE.finditer(text):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return out


def _parse_skip_records(text: str) -> list[dict]:
    """Every `LUSTERNA_CHECK_SKIPPED {json}` record — a declaration a check did NOT run on while the
    finding list still looks complete. Honoured as blocking by callers, never dropped on the floor."""
    out: list[dict] = []
    for m in _SKIP_LINE_RE.finditer(text):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return out


def _target_name_forms(patterns: list[str]) -> list[str]:
    """The dotted-suffix forms an INFER `target_patterns` entry can actually match in generated Lean.

    Those patterns are CHARON MATCHERS, not Lean names: `crate::foo::_::measure`
    uses `crate` for the crate root and `_` as a WILDCARD for the impl/type. Aeneas generates
    `foo.Bar.measure` — the wildcard stands for a component that IS
    present in the Lean name — so rewriting `::`→`.` yields `crate.foo._.measure`,
    which suffix-matches nothing at all. The failure is silent and total: every target lookup comes
    back EMPTY, and a checker told "no targets" reports `found 0` on every theorem while a gate told
    the same reports nothing to reject.

    So the BARE FINAL COMPONENT is what reliably matches, and it is a form the dotted-suffix
    comparison already supports by design. The wildcard-free dotted form is emitted alongside it
    when it differs: that one is stricter, so it matches exactly when a pattern names a function
    fully-qualified. A bare component is looser than a full path — a helper ending in the same
    component answers to it too — which is the accepted cost of patterns that do not name types.
    """
    out: list[str] = []
    for pat in patterns:
        comps = [c for c in (pat or "").replace("::", ".").strip(".").split(".") if c]
        if not comps:
            continue
        for form in (comps[-1],
                     ".".join(c for c in comps if c != "crate") if "_" not in comps else ""):
            if form and form not in out:
                out.append(form)
    return out


def _schema_targets(deps: AgentDeps) -> list[str]:
    """The functions under verification, as DOTTED names `checkSchemaConformance` can suffix-match.
    INFER's `target_patterns` use `a::b::c`; the checker compares by dotted suffix (see
    `nameHasSuffix` in spec_checks.lean), which is the same rewrite `docs/skills/mechanical-checks.md`
    prescribes to agents.

    WHOLE-CRATE mode (empty `target_patterns`) falls back to every crate `def`, exactly as
    `target_defs` does for the legitimacy gate. Passing an empty target list instead would make the
    checker report SKIPPED on every theorem — i.e. "not checked" reading as "nothing to say", which
    is the one thing a gate must never do."""
    if pats := _target_name_forms(deps.progress.get("target_patterns", [])):
        return pats
    try:
        return sorted(target_defs(deps, translation_text(deps)))
    except Exception as e:                # noqa: BLE001 - reported by the caller, never swallowed
        # Returning [] here would be a silent pass: the checker would report SKIPPED on every
        # theorem, the sentinel would still print, and `check_spec_gate` would hand the gate an empty
        # failure list — "conforms" without having checked anything. The caller turns an empty target
        # list into a BLOCKING record instead.
        log.warning("_schema_targets: could not read the translation to derive targets: %s", e)
        return []


def check_spec_gate(deps: AgentDeps, spec_rel: str) -> list[dict]:
    """THE SPEC GATE. Run every mechanical check over the campaign's theorems and return what BLOCKS.

    These checks are not advisory and no agent interprets them: a finding comes back to FORMALISE as
    a concrete critique and the stage runs again. `checkSpecGate` (spec_checks.lean) composes
    conformance and provenance, and holds the precedence and scoping that make blocking sound — a
    theorem whose checked shape it does not match gets that finding alone, and `@[lusterna_lemma]` is
    out of scope by declaration, which is where a relational or two-run property lives.

    What this does NOT establish, and what stays with SPEC-JUDGE: whether a theorem MEANS the right
    thing. A checked property whose projection does not faithfully mirror the code, or a real property
    hidden under `@[lusterna_lemma]`, can conform perfectly — the gate verifies FORM, never fitness.

    Returns a list of blocking records `{check, theorem, schema, rule, detail}`; EMPTY means clear. Mechanism mirrors `check_axioms`: import the already-built spec olean and elaborate a
    throwaway driver (never re-elaborate the spec — that auto-`sorry`s and taints the whole batch),
    and read the `LUSTERNA_SCHEMA_DONE` sentinel, because silence without it means "the driver died",
    never "everything conforms"."""
    original = tools.read_out(deps, spec_rel)
    if original.startswith("ERROR:"):
        return [{"theorem": "?", "schema": "?", "rule": "spec_unreadable", "detail": original}]
    qnames = _theorem_qualified_names(original)
    if not qnames:
        return []
    parts = tools._norm_out(spec_rel).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return [{"theorem": "?", "schema": "?", "rule": "bad_spec_path",
                 "detail": f"unexpected spec path {spec_rel!r}"}]
    lib, spec_module = parts[1], ".".join(parts[1:]).removesuffix(".lean")
    targets = _schema_targets(deps)
    if not targets:
        # Without targets the checker cannot identify an execution, so it SKIPS every theorem. That
        # is "could not check", and it must never reach the gate as an empty failure list.
        return [{"theorem": "?", "schema": "?", "rule": "could_not_check",
                 "detail": "no target functions could be determined (empty `target_patterns` and no "
                           "readable translation), so no theorem could be checked"}]
    tarr = ", ".join("`" + g for g in targets)
    # ANCHOR BRIDGE (fidelity): each real measurement INFER names must be referenced by at least one
    # theorem, so a reconstructed projection is tied to the real function somewhere. Fail-safe: no
    # anchors declared → the check does not run and blocks nothing.
    anchors = _target_name_forms(deps.progress.get("anchor_functions", []))
    qall = ", ".join("`" + q for q in qnames)
    anchor_line = (f"  checkAnchorReference #[{qall}] #[{', '.join('`' + a for a in anchors)}]\n"
                   if anchors else "")
    body = (f"import {spec_module}\nimport {lib}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "set_option maxRecDepth 8000 in\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            + "".join(f"  let _ ← checkSpecGate `{n} #[{tarr}]\n" for n in qnames)
            + anchor_line
            + f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_spec_gate.lean")
    if text.startswith("ERROR:"):
        return [{"theorem": "?", "schema": "?", "rule": "could_not_check", "detail": text}]
    if not _driver_ran(text):
        # NOT "clean". The driver never reached the end, so nothing was established. Conservative
        # and loud, the same way check_axioms treats an unresolved theorem.
        log.warning("check_spec_gate: driver did not finish (rc=%s) — treating as could-not-check, "
                    "not as clear. Lean output tail:\n%s", code, text[-1500:])
        return [{"theorem": "?", "schema": "?", "rule": "could_not_check",
                 "detail": f"the conformance driver did not finish; last output:\n{text[-800:]}"}]
    out: list[dict] = []
    for rec in _parse_check_records(text):
        if rec.get("check") in _GATE_CHECKS:
            out.append({"check": rec.get("check", "?"),
                        **{k: rec.get(k, "?") for k in ("theorem", "schema", "rule", "detail")}})
    # A SKIPPED record names a theorem that went UNCHECKED. Honouring it here is the same rule
    # tests/checklean/verify.py pins for the judge-facing path: "clean" must never mean "never ran",
    # so a skip BLOCKS rather than being dropped on the floor.
    for rec in _parse_skip_records(text):
        if rec.get("check") in _GATE_CHECKS:
            out.append({"check": rec.get("check", "?"), "theorem": rec.get("theorem", "?"),
                        "schema": rec.get("schema", "?"), "rule": "could_not_check",
                        "detail": f"not checked: {rec.get('reason', 'unspecified')}"})
    # An anchor referenced by NO theorem is an ungrounded core: the property reconstructs a real
    # measurement but ties it to the real function nowhere. Blocking, so FORMALISE adds the bridge.
    for rec in _parse_check_records(text):
        if rec.get("check") == "anchor_bridge" and not rec.get("referenced", True):
            anchor = rec.get("anchor", "?")
            out.append({"check": "anchor_bridge", "theorem": "(module)", "schema": "checked",
                        "rule": "unbridged_anchor",
                        "detail": f"no theorem references the measurement `{anchor}` the properties "
                                  f"are stated in terms of — add a checked bridge theorem tying your "
                                  f"projection to `{anchor}` (e.g. `{anchor} … = ok t → <projection> "
                                  f"= t…`), or the core is ungrounded"})
    log.info("check_spec_gate: %d blocking finding(s) over %d theorem(s)", len(out), len(qnames))
    return out


def check_grounding(deps: AgentDeps, established: set[str]) -> dict:
    """POST-PROVE grounding rung of the authoritative verdict. For each measurement INFER named an
    anchor, decide whether an ESTABLISHED theorem references it in a MEASUREMENT position
    (`f … = ok …`, or an Aeneas triple over it) — i.e. whether the projection the properties reason
    about is tied back to the real fallible function by a bridge that is actually PROVED, not merely
    stated.

    This is the SAME `checkAnchorReference` the spec gate runs at FORMALISE, re-timed and re-scoped.
    At FORMALISE every theorem is `sorry`, so the gate can only see that a bridge is WRITTEN; here it
    runs over the established set (`established` = the campaign-qualified `<Campaign>::<name>` clean
    theorems), so `referenced` means "referenced by a theorem that HOLDS". A stated-but-unproven bridge
    (`bridge := by sorry`, or one demoted to tainted) grounds nothing and surfaces here as ungrounded —
    closing the gap the FORMALISE-time check cannot see, because a projection's invariants can be clean
    while the bridge that ties them to reality is not.

    Returns {"anchors": [forms], "grounded": [forms], "ungrounded": [forms]} over the anchor NAME FORMS
    (as the gate uses them). Empty anchors ⇒ nothing to ground (fail-safe, like the gate). A driver
    that cannot run, or an empty established set, leaves every anchor UNGROUNDED — conservative, never
    silently grounded, mirroring how `check_axioms` treats an unresolved theorem."""
    from pathlib import Path as _P
    anchors = _target_name_forms(deps.progress.get("anchor_functions", []))
    if not anchors:
        return {"anchors": [], "grounded": [], "ungrounded": []}
    mods = spec_modules(deps)
    stem = _crate_stem(deps)
    if not mods or not stem:
        return {"anchors": anchors, "grounded": [], "ungrounded": anchors}
    qnames: list[str] = []
    for mod in mods:
        src = tools.read_out(deps, mod)
        if src.startswith("ERROR:"):
            continue
        camp = _P(mod).stem
        for short, qual in zip(_theorem_names(src), _theorem_qualified_names(src)):
            if f"{camp}::{short}" in established:
                qnames.append(qual)
    if not qnames:                       # nothing established ⇒ no theorem can ground an anchor
        return {"anchors": anchors, "grounded": [], "ungrounded": anchors}
    imports = "".join(f"import {stem}.Spec.{_P(m).stem}\n" for m in mods)
    body = (imports + f"import {stem}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "set_option maxRecDepth 8000 in\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            f"  checkAnchorReference #[{', '.join('`' + q for q in qnames)}] "
            f"#[{', '.join('`' + a for a in anchors)}]\n"
            f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_grounding_check.lean")
    if text.startswith("ERROR:") or not _driver_ran(text):
        log.warning("check_grounding: driver did not finish (rc=%s) — every anchor conservatively "
                    "UNGROUNDED. Lean output tail:\n%s", code, text[-800:])
        return {"anchors": anchors, "grounded": [], "ungrounded": anchors}
    seen: dict[str, bool] = {}
    for rec in _parse_check_records(text):
        if rec.get("check") == "anchor_bridge":
            seen[rec.get("anchor", "?")] = bool(rec.get("referenced", False))
    grounded = [a for a in anchors if seen.get(a, False)]
    ungrounded = [a for a in anchors if not seen.get(a, False)]
    log.info("check_grounding: %d/%d anchor form(s) grounded by an established theorem (ungrounded: %s)",
             len(grounded), len(anchors), ungrounded or "none")
    return {"anchors": anchors, "grounded": grounded, "ungrounded": ungrounded}


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

    _container_write(deps, f"{lean_out_dir}/lakefile.lean", lakefile)

    # Use the pre-resolved manifest and toolchain file from the template so lake
    # knows the pinned package set without any network access.
    _container_exec(deps, ["cp", f"{LEAN_TEMPLATE}/lake-manifest.json",
                           f"{lean_out_dir}/lake-manifest.json"])
    _container_exec(deps, ["cp", f"{LEAN_TEMPLATE}/lean-toolchain",
                           f"{lean_out_dir}/lean-toolchain"])

    # Symlink the pre-downloaded package trees (Mathlib, Aeneas runtime, etc.)
    _container_exec(deps, ["mkdir", "-p", f"{lean_out_dir}/.lake"])
    _container_exec(deps, ["ln", "-sfn", f"{LEAN_TEMPLATE}/.lake/packages",
                           f"{lean_out_dir}/.lake/packages"])

    log.info("Generated lakefile.lean + package symlinks for crate '%s'", crate)



_BUILD_TAIL = 200  # lines of stderr to keep on failure — errors appear at the end
# Cap on the HARNESS's own `lake build`/`lake env lean` gate (NOT the agent — that is un-clocked and
# budget-bounded). A legitimate full build of a large multi-module library against prebuilt Mathlib
# takes minutes, so this is set generously (matching the agent's own `timeout 900 lake build`): it is
# only a stuck-build backstop — e.g. a pathological `native_decide` evaluating naive recursion —
# never a work limiter (a false timeout here would wrongly report a good tree as non-compiling/tainted).
_BUILD_TIMEOUT = int(os.environ.get("LUSTERNA_BUILD_TIMEOUT", "900"))


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

    A whole-project `lake build` here would report on more than the translation: the lakefile's
    `globs := .andSubmodules` target also picks up whatever else sits under `<Crate>/` — the
    checks tool `setup_lake` provisions, and later FORMALISE's Spec module. `lake env lean`
    compiles just this file in the project environment (imports resolve against the prebuilt
    Aeneas packages), so a failure here is always the translation's."""
    return _run_lake(deps, ["env", "lean", f"{OUT_IN}/{lean_path}"],
                     timeout_msg=f"lake env lean exceeded {_BUILD_TIMEOUT}s")


# ── implementation-spec operations ─────────────────────────────────────────────

def _crate_stem(deps: AgentDeps) -> str:
    """The Aeneas crate module name (e.g. `Metavault`) from the committed translation root."""
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    return lean_path.rsplit("/", 1)[-1].removesuffix(".lean") if lean_path else ""


def campaign_spec(deps: AgentDeps) -> str:
    """The CURRENT campaign's spec module — lean/<Crate>/Spec/<Campaign>.lean. Each campaign is a
    SEPARATE module in its own namespace, so runs ACCUMULATE (a new campaign adds a file) instead of
    overwriting one Spec.lean. The Aeneas lib's `andSubmodules` glob builds them all, no lakefile
    change. Deterministic (campaign from the instruction filename), never agent-chosen."""
    stem = _crate_stem(deps)
    return f"lean/{stem}/Spec/{deps.campaign}.lean" if stem else ""


def spec_modules(deps: AgentDeps) -> list[str]:
    """Every campaign spec module present under lean/<Crate>/Spec/ — the cumulative set the
    `#print axioms` verdict spans, so prior campaigns' established theorems keep counting."""
    stem = _crate_stem(deps)
    if not stem:
        return []
    _, out, _ = exec_in(deps.container_id,
                        ["sh", "-c", f"find {OUT_IN}/lean/{stem}/Spec -maxdepth 1 -name '*.lean' "
                                     f"2>/dev/null | sort"])
    pre = OUT_IN + "/"
    return [l.strip().removeprefix(pre) for l in out.splitlines() if l.strip()]


def assumptions_module(deps: AgentDeps) -> str:
    """The shared TRUSTED-BASE module — lean/<Crate>/Assumptions.lean. Holds ONLY declared `axiom`s:
    general facts about the SUBSTRATE (never a target property) that a proof genuinely cannot
    discharge. Cumulative across campaigns like the translation. `#print axioms` still reports every
    use, so the trust is disclosed, gated (legitimacy_check), and retirable — never hidden. Domain-
    neutral by design: nothing here presumes the intractable facts are arithmetic."""
    stem = _crate_stem(deps)
    return f"lean/{stem}/Assumptions.lean" if stem else ""


def declared_assumptions(deps: AgentDeps) -> set[str]:
    """Fully-qualified names of the `axiom`s declared in the assumptions module — the trusted base
    `check_axioms` recognises as `assumed` rather than `tainted`. Empty when the module is absent, so
    a run with no trusted base behaves exactly as the prior binary clean/tainted gate."""
    mod = assumptions_module(deps)
    if not mod:
        return set()
    text = tools.read_out(deps, mod)
    return set() if text.startswith("ERROR:") else set(_qualified_axiom_names(text))


def target_defs(deps: AgentDeps, translation: str) -> set[str]:
    """Translation def names corresponding to the INFER `target_patterns` — the functions actually
    under verification. A pattern `a::b::c` matches a translated def whose dotted name equals or ends
    with `a.b.c`. legitimacy_check forbids an assumption from referencing any of these, which is what
    makes 'the goal is never relaxed' mechanical: a GOAL states a property OF a target, so an axiom
    that may reference no target can never be a goal.

    WHOLE-CRATE mode (empty `target_patterns`): every crate `def` is a target, so no assumption about
    crate code can pass — only facts about the truly-external substrate (Aeneas emits those as
    `axiom`s, not `def`s, so they are not in this set) remain admissible. Without this the legitimacy
    gate would be VACUOUS exactly when everything is a goal."""
    pats = _target_name_forms(deps.progress.get("target_patterns", []))
    defs = set(_def_blocks(translation))
    if not pats:
        return defs
    return {d for d in defs if any(d == p or d.endswith("." + p) for p in pats)}


_LEGIT_TAINT_ALL = "*"   # sentinel axiom name: could-not-check ⇒ the whole trusted base is rejected


def legitimacy_check(deps: AgentDeps, translation: str) -> list[dict]:
    """Mechanical admissibility of the declared trusted base — NOT a proof-quality judgement. Returns
    one violation record `{"axiom": <qualified name>, "targets": [...], "reason": <str>}` per
    illegitimate `axiom`; empty ⇒ the base is admissible. An axiom is illegitimate iff its type
    references a target function: the trusted base may only ever hold facts about the substrate, never
    a property of the code under verification — so a GOAL (a property OF a target) can never be
    admitted. Keyed by the fully-qualified name, so callers fail-closed by matching it against the
    `assumed` dependency lists from `check_axioms`. (A rogue `axiom` declared OUTSIDE this module is
    not in `declared_assumptions`, so `check_axioms` already taints anything leaning on it.)

    Mechanism (MetaM, replacing the former text scan): a throwaway driver imports the built assumptions
    olean and calls `checkAssumptionLegitimacy` over EXACTLY the axioms `check_axioms` honours
    (`declared_assumptions`), which walks each axiom's ELABORATED type — so a target reached through
    notation, a coercion, an `abbrev`, or a re-export is caught, which the surface-text scan missed.
    FAIL-CLOSED: if the driver does not finish (no sentinel), the base could not be certified
    admissible, so the whole base is rejected via the `_LEGIT_TAINT_ALL` sentinel record — a caller
    that fails open here would let an unchecked assumption relax a goal."""
    mod = assumptions_module(deps)
    if not mod:
        return []
    axioms = sorted(declared_assumptions(deps))
    if not axioms:
        return []
    parts = tools._norm_out(mod).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return [{"axiom": _LEGIT_TAINT_ALL, "targets": [],
                 "reason": f"unexpected assumptions path {mod!r}; base rejected fail-closed"}]
    lib, assum_module = parts[1], ".".join(parts[1:]).removesuffix(".lean")
    # Same target forms as `target_defs`: pattern suffixes, or EMPTY = whole-crate mode (every crate
    # def is a target). The MetaM check applies the identical rule per mode, so the verdict matches.
    targets = _target_name_forms(deps.progress.get("target_patterns", []))
    axlist = ", ".join("`" + a for a in axioms)
    tlist = ", ".join("`" + t for t in targets)
    body = (f"import {assum_module}\nimport {lib}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "set_option maxRecDepth 8000 in\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            f"  checkAssumptionLegitimacy #[{axlist}] #[{tlist}]\n"
            f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_legitimacy_check.lean")
    if text.startswith("ERROR:") or not _driver_ran(text):
        log.warning("legitimacy_check: driver did not finish (rc=%s) — FAILING CLOSED, the whole "
                    "trusted base is rejected. Lean tail:\n%s", code, text[-1200:])
        return [{"axiom": _LEGIT_TAINT_ALL, "targets": [],
                 "reason": "the legitimacy driver did not finish; the trusted base could not be "
                           "certified admissible, so it is rejected"}]
    out: list[dict] = []
    for rec in _parse_check_records(text):
        if rec.get("check") == "assumption_legitimacy":
            out.append({"axiom": rec.get("axiom", "?"), "targets": rec.get("targets", []),
                        "reason": rec.get("reason", "")})
    return out


_REFUTATION_SUFFIX = "__refuted"


def refutations_module(deps: AgentDeps) -> str:
    """Module where PROVE records a REFUTATION — a proof of `¬<statement>` — for a theorem it found
    false. A refutation lemma is `theorem <written-theorem-name>__refuted : ¬ <that statement> := by …`.
    It is verified like any proof (compiles, no `sorryAx`), but `decide`/`native_decide` is PERMITTED:
    a refutation evaluates a concrete finite counterexample (unlike a general proof, where
    native_decide's compiler-trust taints). A verified refutation is a kernel-checked DISCREPANCY —
    the property does not hold for the code as written — surfaced prominently in the report as a
    candidate finding warranting investigation. The dual of the `#print axioms` proof gate; it does
    NOT amend the spec (statements are FORMALISE's, and the spec was already independently judged)."""
    stem = _crate_stem(deps)
    return f"lean/{stem}/Refutations.lean" if stem else ""


def _module_of(rel: str) -> str:
    """`lean/<A>/<B>.lean` → the Lean module name `<A>.<B>` (the lean_lib import path)."""
    parts = tools._norm_out(rel).split("/")
    return ".".join(parts[1:]).removesuffix(".lean") if len(parts) >= 2 and parts[0] == "lean" else ""


def campaign_spec_module(deps: AgentDeps) -> str:
    """The Lean module name of THIS campaign's spec file (e.g. `Crate.Spec.Campaign`) — the module the
    statement-drift fingerprint is dumped over, at FORMALISE and again post-PROVE."""
    return _module_of(campaign_spec(deps))


def verify_refutations(deps: AgentDeps) -> list[str]:
    """WRITTEN theorem names the refutations module MECHANICALLY establishes FALSE. For each
    `theorem <name>__refuted : ¬ …` it applies TWO checks, both required:
      • TYPE TIE — `example : False := <name>__refuted <name>` type-checks. This holds iff
        `<name>__refuted` refutes the EXACT statement of `<name>` (application forces the negated type
        to equal `<name>`'s type), so the agent cannot refute a strawman and weaken a true theorem.
        (`<name>` being `sorry`-proved is irrelevant — it is used only as a term of its type.)
      • PURITY — `#print axioms <name>__refuted` shows NO `sorryAx`, so the refutation is a real proof
        of the negation, not a faked/incomplete one. `native_decide` IS allowed: a refutation is a
        concrete finite counterexample, not a general proof.
    A refutation passing both means `<name>` is genuinely false as stated — a kernel-verified
    discrepancy to REPORT as a headline finding (not a trigger to amend the spec). Conservative: if
    the checker does not compile (a tie failed), NO refutation is honored this round."""
    rmod = refutations_module(deps)
    rtext = tools.read_out(deps, rmod)
    if rtext.startswith("ERROR:"):
        return []
    ref_pairs = [(n, q) for n, q in zip(_theorem_names(rtext), _theorem_qualified_names(rtext))
                 if n.endswith(_REFUTATION_SUFFIX)]
    if not ref_pairs:
        return []
    spec = campaign_spec(deps)
    stext = tools.read_out(deps, spec)
    orig_qual = dict(zip(_theorem_names(stext), _theorem_qualified_names(stext)))
    # each refutation must target an EXISTING theorem of THIS campaign's spec
    checks = [(n[:-len(_REFUTATION_SUFFIX)], q, orig_qual[n[:-len(_REFUTATION_SUFFIX)]])
              for n, q in ref_pairs if n[:-len(_REFUTATION_SUFFIX)] in orig_qual]
    if not checks:
        return []
    if not build(deps).get("success"):
        log.warning("verify_refutations: project does not compile with %s — no refutation honored", rmod)
        return []
    rmodule, smodule = _module_of(rmod), _module_of(spec)
    lib = _crate_stem(deps)
    if not rmodule or not smodule or not lib:
        return []
    # Two gates in one driver: the TYPE-TIE `example : False := <ref> <orig>` per refutation is
    # enforced by the driver COMPILING (it forces the negation to the EXACT statement — all-or-nothing,
    # so any tie failure fails the file and honours nothing), and PURITY (no `sorryAx`) is decided by
    # `refutationPurity` via `collectAxioms`, emitted as records.
    ties = "\n".join(f"example : False := {ref_q} {orig_q}" for _, ref_q, orig_q in checks)
    reflist = ", ".join(f"`{ref_q}" for _, ref_q, _ in checks)
    body = (f"import {smodule}\nimport {rmodule}\nimport {lib}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n\n"
            f"{ties}\n\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            f"  refutationPurity #[{reflist}]\n"
            f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_refutation_check.lean")
    if code != 0 or not _driver_ran(text):
        log.warning("verify_refutations: type-tie/checker did not compile — no refutation honored "
                    "(a refutation must prove ¬ the EXACT statement). Lean tail:\n%s", text[-1200:])
        return []
    pure_lemmas = {r.get("lemma") for r in _parse_check_records(text)
                   if r.get("check") == "refutation" and r.get("pure") is True}
    refuted = [target for target, ref_q, _ in checks if ref_q in pure_lemmas]
    if refuted:
        log.info("verify_refutations: %d theorem(s) mechanically REFUTED (false as stated): %s",
                 len(refuted), refuted)
    return refuted


# ── statement-drift detector ───────────────────────────────────────────────────────────────────────
# FORMALISE fixes the theorem STATEMENTS (committed as "implementation spec (statements only)") and
# PROVE fills in the proofs. PROVE is told to prove them "WITHOUT changing any statement", but that is
# a directive, not a gate: an agent can quietly WEAKEN a true statement to make it provable — the
# "moved the goalposts" cheat. This detector compares each FORMALISE statement against its PROVE form
# and classifies the drift. It does NOT block: a statement legitimately CHANGES when it is found false
# and REFUTED (the P5-style correction), so a change backed by a `<name>__refuted` witness is
# ACCOUNTED; a change with no witness is raised to REVIEW. Detect and account, never forbid.
#
# The comparison is done in LEAN, never by scanning source: `dumpStatementTypes` enumerates the
# theorems the campaign module compiled into and emits a STRUCTURAL HASH of each elaborated TYPE (the
# statement, proof irrelevant). The fingerprint is captured at FORMALISE into progress and diffed
# against a fresh dump post-PROVE. Python only compares the Lean-produced hashes — no regex over Lean
# statements, which is brittle (multiline attributes, unicode, binder defaults, `open`/namespacing).


def dump_statement_types(deps: AgentDeps, module: str) -> dict[str, str]:
    """{fully-qualified theorem name → structural hash of its elaborated TYPE} for every theorem the
    Lean module *module* (e.g. `Crate.Spec.Campaign`) compiled into. The hash is produced by
    `dumpStatementTypes` in Lean over `ConstantInfo.type`, so two builds of the SAME statement yield
    the SAME hash regardless of how it is proved or pretty-printed, and a CHANGED statement yields a
    different one. Returns {} if the driver cannot run (the caller treats that conservatively)."""
    stem = _crate_stem(deps)
    if not stem or not module:
        return {}
    body = (f"import {module}\n"
            f"import {stem}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            f"  dumpStatementTypes `{module}\n"
            f'  IO.println "{_GATE_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_statement_types.lean")
    if text.startswith("ERROR:") or not _driver_ran(text):
        log.warning("dump_statement_types: driver did not finish (rc=%s) for %s. Lean tail:\n%s",
                    code, module, text[-600:])
        return {}
    out: dict[str, str] = {}
    for rec in _parse_check_records(text):
        if rec.get("check") == "statement_type" and rec.get("theorem"):
            out[rec["theorem"]] = str(rec.get("hash", ""))
    return out


def _classify_drift(old: dict[str, str], new: dict[str, str], refuted: set[str]) -> dict:
    """Classify each FORMALISE theorem by comparing its type FINGERPRINT (from *old*) against the
    current one (*new*), keyed by fully-qualified name. A theorem is:
      • stable    — present with an identical type hash;
      • accounted — type changed OR theorem gone, AND a refutation witness exists (its short name is
                    in *refuted*) → a legitimate, recorded correction (the P5 path);
      • drifted   — type changed with NO refutation witness → raise to REVIEW;
      • removed   — theorem gone with NO refutation witness → raise to REVIEW.
    Theorems only in *new* are PROVE-added supporting lemmas — expected, not reported. Reported names
    are the fully-qualified constant names Lean emitted."""
    stable, drifted, removed, accounted = [], [], [], []
    for qual, old_hash in old.items():
        short = qual.rsplit(".", 1)[-1]
        changed = qual not in new or new[qual] != old_hash
        if not changed:
            stable.append(qual)
        elif short in refuted:
            accounted.append(qual)
        elif qual not in new:
            removed.append(qual)
        else:
            drifted.append(qual)
    return {"stable": stable, "drifted": drifted, "removed": removed, "accounted": accounted}


def check_statement_drift(deps: AgentDeps, refuted: list[str]) -> dict:
    """POST-PROVE fidelity check: did PROVE alter or drop any statement the FORMALISE spec fixed?

    Diffs the FORMALISE type fingerprints (`progress['formalise_types']`, captured by
    `_stage_formalise` right after the spec compiled) against a fresh dump over the same module. A
    changed or removed statement is ACCOUNTED when a `<name>__refuted` witness exists for it (*refuted*,
    the short names from `verify_refutations`) and otherwise surfaces as drift for REVIEW. Returns
    {"checked", "stable", "drifted", "removed", "accounted"}. `checked` is False (conservative, never a
    silent pass) when no FORMALISE fingerprint was captured or the fresh dump could not run."""
    old = deps.progress.get("formalise_types") or {}
    if not old:
        return {"checked": False, "stable": [], "drifted": [], "removed": [], "accounted": []}
    new = dump_statement_types(deps, campaign_spec_module(deps))
    if not new:
        return {"checked": False, "stable": [], "drifted": [], "removed": [], "accounted": []}
    res = _classify_drift(old, new, set(refuted or []))
    res["checked"] = True
    log.info("check_statement_drift: %d stable, %d drifted, %d removed, %d accounted-by-refutation",
             len(res["stable"]), len(res["drifted"]), len(res["removed"]), len(res["accounted"]))
    return res


def build(deps: AgentDeps) -> dict:
    """Run `lake build` over the whole Lean project (all campaign spec modules) and record it."""
    result = check_lean(deps, campaign_spec(deps))
    deps.progress["lean_build"] = result
    deps.progress["build_seq"] = deps.progress.get("build_seq", 0) + 1
    checkpoint.snapshot(deps)
    return result


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
