"""Aeneas/Lean operations and analysis: translation, build, the `#print axioms` gate, and
spec assembly helpers. The heavy Lean logic, kept out of the trivial file tools in tools.py."""
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from . import checkpoint, tools
from .container import exec_in, OUT_IN
from .schemas import AgentDeps

log = logging.getLogger(__name__)


def _container_exec(deps: AgentDeps, cmd_args: list[str]) -> None:
    """Run a provisioning command in the container, raising on failure. For the harness's own
    file wiring (the lakefile, the checks tool) — `container.exec_in` is for commands whose
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
    # slow (one `cat` per file) AND pollute translation_text/external_axioms/referenced_defs with all
    # of Mathlib. The translation is only lean/<Crate>.lean + lean/<Crate>/**.
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
# would enter `_def_blocks` (hence `referenced_defs`, `target_defs`, the hole scan).
_LINT_TOOL_NAME = "LusternaChecks.lean"
# The SCHEMA ATTRIBUTES live in their own file, and the split is load-bearing: a spec module
# `import`s this one to write `@[lusterna_hoare]`, which makes its attribute names a stable public
# API, while prior campaigns' spec modules are immutable (`pipeline._restore_prior_specs`). Keeping
# the churn-prone checker logic in LusternaChecks means it can be rewritten freely without risking
# an old spec module's build. See the append-only rule in spec_schemas.lean's own header.
_SCHEMA_TOOL_NAME = "LusternaSchemas.lean"
_HARNESS_OWNED_LEAN = {"lakefile.lean", _LINT_TOOL_NAME, _SCHEMA_TOOL_NAME}

_LINT_TOOL_SRC = (Path(__file__).parent / "docs" / "tools" / "spec_checks.lean").read_text()
_SCHEMA_TOOL_SRC = (Path(__file__).parent / "docs" / "tools" / "spec_schemas.lean").read_text()


def _write_lint_tool(deps: AgentDeps, lean_out_dir: str, lib_name: str) -> None:
    """Copy the standalone mechanical-checks tool (`docs/tools/spec_checks.lean`) into this
    crate's own lean tree, at `lean/<lib_name>/LusternaChecks.lean` — module
    `<lib_name>.LusternaChecks`. It only depends on Aeneas (crate-agnostic), so it compiles
    unchanged for every crate; placing it under the crate's own lib means `lake build` picks it
    up automatically via the existing `.andSubmodules` glob, no separate build step. The flip side
    of that convenience: the harness's own compile gate now typechecks it, so a Lean/Aeneas
    metaprogramming-API drift surfaces as a build failure — tests/checklean/verify.py is what
    catches that before a campaign does.

    Writes TWO files. `LusternaSchemas.lean` registers the `@[lusterna_invariant]` /
    `@[lusterna_hoare]` / `@[lusterna_freeform]` attributes and is what a SPEC MODULE imports;
    `LusternaChecks.lean` imports it and holds the checks. Schema conformance is the one thing here
    the harness may itself gate on (`check_schemas`), because a theorem failing the schema it
    DECLARED is a fact, not a judgement — everything else below stays judge-only.

    The other three checks are NOT a harness gate. They are a TOOL an agent (SPEC-JUDGE, primarily) may invoke itself
    over Bash — `import <lib_name>.LusternaChecks`, `open Lusterna.Checks`, call
    `checkAssumedPostcondition`/`checkInvariantTotality`/`checkSchemaConformance` — and interpret the
    findings with judgment; see docs/skills/mechanical-checks.md. The harness never runs it and
    never parses its output. HARNESS-OWNED like the lakefile: an agent must not edit or delete it,
    and (like the lakefile) it is rewritten on every `setup_lake`.
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


def _strip_lean_comments(s: str) -> str:
    """Drop Lean block comments (incl. `/-- … -/` docstrings) and line comments, so a
    following def's docstring — swallowed into a block — can't create a false call edge."""
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


def sorry_bodied_theorems(spec_text: str) -> set[str]:
    """Short names of theorems/lemmas whose proof BODY still contains a literal `sorry` — i.e.
    genuinely-open obligations, as opposed to theorems that compile but are tainted by a
    non-standard axiom (native_decide etc.). Used to split the tainted set for PROVE feedback.
    Each declaration spans from its `theorem`/`lemma` keyword to the next declaration or EOF."""
    decls = list(_THEOREM_DECL_RE.finditer(spec_text))
    out: set[str] = set()
    for i, m in enumerate(decls):
        end = decls[i + 1].start() if i + 1 < len(decls) else len(spec_text)
        body = _strip_lean_comments(spec_text[m.end():end])   # a `sorry` in a comment is not an obligation
        if re.search(r"\bsorry\b", body):
            out.add(m.group(1).split(".")[-1])
    return out


def stub_proofs(text: str) -> str:
    """Force every `theorem`/`lemma` proof body to `:= by sorry`, preserving statements,
    definitions, imports and docstrings. FORMALISE emits statement-only structured output,
    so its theorems never carry proofs; this is a safety net for any stray theorem the
    model puts in the free-form `preamble` — keeping proofs (and pathological tactics like
    `native_decide`) out of the spec until the PROVE stage."""
    lines = text.split("\n")
    decl = re.compile(r"^\s*" + _DECL_PREFIX + r"(?:theorem|lemma)\b")
    # `\b` on the KEYWORDS only: the symbolic openers (`@[`, `/-`, `--`, `#`) end in a
    # non-word character, where `\b` would fail against the space that usually follows.
    newtop = re.compile(r"^\s*(?:(?:theorem|lemma|def|abbrev|noncomputable|instance|structure|"
                        r"inductive|namespace|end|section|open|variable|import|"
                        r"private|protected|nonrec|scoped|partial|unsafe)\b|@\[|/-|--|#)")
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


_AXIOM_VERDICT_RE = re.compile(
    r"'([\w.]+)' (?:(does not depend on any axioms)|depends on axioms: \[(.*?)\])", re.DOTALL)


def _parse_axiom_verdicts(text: str) -> dict[str, set[str]]:
    """Parse `#print axioms` output into {fully-qualified name → its axiom-name set} (empty set = "does
    not depend on any axioms"). Keyed by the FULL qualified name the checker printed, NEVER the last
    dotted component: dotted theorem names (`mint_shares.spec`, `deposit.spec`, …) collide on their
    tail (`spec`) and would silently inherit one another's verdict. Lean wraps a long axiom list across
    lines, so the regex is DOTALL and the list is re-joined before splitting."""
    return {m.group(1): (set() if m.group(2)
                         else {a.strip() for a in m.group(3).replace("\n", " ").split(",") if a.strip()})
            for m in _AXIOM_VERDICT_RE.finditer(text)}


def _empty_axioms(raw: str = "") -> dict:
    """The empty check_axioms result — the canonical 3-way shape every early return and every caller
    relies on (nothing clean/assumed/tainted). Keeping it in one place stops an early return from
    silently omitting a key (e.g. `assumed`) that downstream code indexes."""
    return {"clean": [], "assumed": {}, "tainted": [], "raw": raw}


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

    Mechanism: write a throwaway checker that IMPORTS the already-built `Spec.olean` and
    runs `#print axioms` against it, then elaborate just that checker with `lake env lean`.
    The spec is never re-elaborated — no proofs (or `native_decide`) rerun, and imports
    resolve from the compiled artifacts the pipeline already built. The checker is deleted
    afterwards. (Re-elaborating the spec from source instead is fragile: one import or
    proof failure auto-`sorry`s every declaration and taints the whole batch.)
    """
    original = tools.read_out(deps, spec_rel)
    if original.startswith("ERROR:"):
        return _empty_axioms(original)
    names = _theorem_names(original)
    if not names:
        return _empty_axioms()
    # Query `#print axioms` by the NAMESPACE-QUALIFIED name (parallel to `names`) — a bare name
    # inside `namespace Foo` is `Unknown constant` and would taint every theorem.
    qnames = _theorem_qualified_names(original)

    # spec_rel = lean/<Lib>/Spec.lean  →  spec module <Lib>.Spec (matches the lean_lib root)
    parts = tools._norm_out(spec_rel).split("/")
    if len(parts) < 3 or parts[0] != "lean":
        return _empty_axioms(f"ERROR: unexpected spec path {spec_rel!r}")
    spec_module = ".".join(parts[1:]).removesuffix(".lean")

    # Query `#print axioms` by qualified name, parse the verdicts keyed by that full qname, then map
    # back to theorems (see _run_lean_checker / _parse_axiom_verdicts). A qname absent from the parse
    # (`verdict.get` → None) is UNRESOLVED — the checker couldn't read it back — conservatively tainted.
    body = f"import {spec_module}\n\n" + "\n".join(f"#print axioms {n}" for n in qnames) + "\n"
    _, text = _run_lean_checker(deps, body, "_axiom_check.lean")
    if text.startswith("ERROR:"):
        return _empty_axioms(text)
    verdict = _parse_axiom_verdicts(text)
    declared = declared_assumptions(deps)   # the trusted base — declared `axiom`s in the module
    clean, assumed, tainted, unresolved = [], {}, [], []
    for written, qual in zip(names, qnames):    # parallel lists, same order
        axset = verdict.get(qual)
        if axset is None:                        # the checker couldn't read this one back
            tainted.append(written)
            unresolved.append(written)
            continue
        non_std = axset - _STD_AXIOMS
        if not non_std:                          # standard axioms only
            clean.append(written)
        elif non_std <= declared:                # rests only on declared trusted assumptions
            assumed[written] = sorted(non_std)
        else:                                    # sorryAx / native_decide / an UNdeclared axiom
            tainted.append(written)
    if unresolved:
        # Not a soundness signal — the checker couldn't read these back. Surface it loudly
        # instead of silently reporting them as tainted.
        log.warning("check_axioms: %d/%d theorem(s) UNRESOLVED (conservatively tainted, not a "
                    "real axiom finding): %s — lean output tail:\n%s",
                    len(unresolved), len(names), unresolved, text[-1200:])
    log.info("check_axioms: %d clean, %d assumed (modulo %d declared), %d tainted, of %d theorem(s)",
             len(clean), len(assumed), len(declared), len(tainted), len(names))
    return {"clean": clean, "assumed": assumed, "tainted": tainted, "raw": text[-3000:]}


# The `LUSTERNA_CHECK {...}` line the tool emits. Lean prefixes `#eval` output with a
# `file:line:col: info:` header, so both are optional — the same shape tests/checklean/verify.py
# parses, deliberately, so the harness reads exactly what an agent reads.
_CHECK_LINE_RE = re.compile(
    r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK\s+(\{.*\})\s*$")

# A SKIPPED record means one theorem went unanalysed while the finding list still looks complete —
# the ambiguity the token exists to expose.
_SKIP_LINE_RE = re.compile(
    r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK_SKIPPED\s+(\{.*\})\s*$")

_SCHEMA_DONE = "LUSTERNA_SCHEMA_DONE"


def _schema_targets(deps: AgentDeps) -> list[str]:
    """The functions under verification, as DOTTED names `checkSchemaConformance` can suffix-match.
    INFER's `target_patterns` use `a::b::c`; the checker compares by dotted suffix (see
    `nameHasSuffix` in spec_checks.lean), which is the same rewrite `docs/skills/mechanical-checks.md`
    prescribes to agents.

    WHOLE-CRATE mode (empty `target_patterns`) falls back to every crate `def`, exactly as
    `target_defs` does for the legitimacy gate. Passing an empty target list instead would make the
    checker report SKIPPED on every theorem — i.e. "not checked" reading as "nothing to say", which
    is the one thing a gate must never do."""
    pats = [p.replace("::", ".").strip(".") for p in deps.progress.get("target_patterns", []) if p]
    if pats:
        return pats
    try:
        return sorted(target_defs(deps, translation_text(deps)))
    except Exception as e:                # noqa: BLE001 - reported by the caller, never swallowed
        # Returning [] here would be a silent pass: the checker would report SKIPPED on every
        # theorem, the sentinel would still print, and `check_schemas` would hand the gate an empty
        # failure list — "conforms" without having checked anything. The caller turns an empty target
        # list into a BLOCKING record instead.
        log.warning("_schema_targets: could not read the translation to derive targets: %s", e)
        return []


def check_schemas(deps: AgentDeps, spec_rel: str) -> list[dict]:
    """Verify every spec theorem against the schema IT DECLARED (`@[lusterna_invariant]` /
    `@[lusterna_hoare]` / `@[lusterna_freeform "why"]`).

    THE ONE MECHANICAL CHECK THE HARNESS MAY GATE ON. The other three in `spec_checks.lean` are
    judge-only because their findings need judgment — a flagged hypothesis may be a legitimate
    relational premise. Conformance is different in kind: a theorem that fails the schema its own
    author declared is a FACT, so there is nothing for a judge to weigh and a retry message can be
    mechanical. What this does NOT establish is that the declared schema is the RIGHT one for the
    property — that stays with SPEC-JUDGE.

    Returns a list of failure records `{theorem, schema, rule, detail}`; EMPTY means every theorem
    conforms. Mechanism mirrors `check_axioms`: import the already-built spec olean and elaborate a
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
    body = (f"import {spec_module}\nimport {lib}.{_LINT_TOOL_NAME.removesuffix('.lean')}\n"
            "open Lusterna.Checks\n"
            "set_option maxRecDepth 8000 in\n"
            "#eval show Lean.Meta.MetaM Unit from do\n"
            + "".join(f"  let _ ← checkSchemaConformance `{n} #[{tarr}]\n" for n in qnames)
            + f'  IO.println "{_SCHEMA_DONE}"\n')
    code, text = _run_lean_checker(deps, body, "_schema_check.lean")
    if text.startswith("ERROR:"):
        return [{"theorem": "?", "schema": "?", "rule": "could_not_check", "detail": text}]
    if _SCHEMA_DONE not in text:
        # NOT "clean". The driver never reached the end, so nothing was established. Conservative
        # and loud, the same way check_axioms treats an unresolved theorem.
        log.warning("check_schemas: driver did not finish (rc=%s) — treating as could-not-check, "
                    "not as conforming. Lean output tail:\n%s", code, text[-1500:])
        return [{"theorem": "?", "schema": "?", "rule": "could_not_check",
                 "detail": f"the conformance driver did not finish; last output:\n{text[-800:]}"}]
    out: list[dict] = []
    for m in _CHECK_LINE_RE.finditer(text):
        try:
            rec = json.loads(m.group(1))
        except ValueError:
            continue
        if rec.get("check") == "schema_conformance":
            out.append({k: rec.get(k, "?") for k in ("theorem", "schema", "rule", "detail")})
    # A SKIPPED record names a theorem that went UNCHECKED. Honouring it here is the same rule
    # tests/checklean/verify.py pins for the judge-facing path: "clean" must never mean "never ran",
    # so a skip BLOCKS rather than being dropped on the floor.
    for m in _SKIP_LINE_RE.finditer(text):
        try:
            rec = json.loads(m.group(1))
        except ValueError:
            continue
        if rec.get("check") == "schema_conformance":
            out.append({"theorem": rec.get("theorem", "?"), "schema": rec.get("schema", "?"),
                        "rule": "could_not_check",
                        "detail": f"not checked: {rec.get('reason', 'unspecified')}"})
    log.info("check_schemas: %d/%d theorem(s) do not conform to their declared schema",
             len(out), len(qnames))
    return out


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


def theorem_statement(spec_text: str, name: str) -> str:
    """The statement text of theorem *name* — binders + proposition, up to (not including) the
    proof `:=`. Used to decide whether a theorem references the implementation (referenced_defs on
    it). Returns '' if not found.

    Carries `_DECL_PREFIX` and accepts `lemma` like every other declaration matcher here: a name
    that `_theorem_names` yields must resolve to a statement, or `_verifies_impl` reads '' as "no
    translated def referenced" and the report's headline count silently drops the theorem."""
    m = re.search(r"(?m)^[ \t]*" + _DECL_PREFIX + rf"(?:theorem|lemma)\s+{re.escape(name)}\b",
                  spec_text)
    if not m:
        return ""
    tail = spec_text[m.start():]
    # Cut at this theorem's proof `:=` via the bracket-aware splitter, so a nested record-update
    # `:=` survives (a naive find(':=') would truncate the statement mid-expression). If there is
    # no top-level `:=` at all, stop at the blank line so we don't run into the next theorem.
    stmt = _proposition_only(tail)
    return stmt if stmt != tail.rstrip() else tail.split("\n\n", 1)[0]


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
    pats = [p.replace("::", ".").strip(".") for p in deps.progress.get("target_patterns", []) if p]
    defs = set(_def_blocks(translation))
    if not pats:
        return defs
    return {d for d in defs if any(d == p or d.endswith("." + p) for p in pats)}


def legitimacy_check(deps: AgentDeps, translation: str) -> list[dict]:
    """Mechanical admissibility of the declared trusted base — NOT a proof-quality judgement. Returns
    one violation record `{"axiom": <qualified name>, "targets": [...], "reason": <str>}` per
    illegitimate `axiom`; empty ⇒ the base is admissible. An axiom is illegitimate iff its statement
    references a target function (`target_defs`): the trusted base may only ever hold facts about the
    substrate, never a property of the code under verification — so a GOAL (a property OF a target)
    can never be admitted. Keyed by the fully-qualified name, so callers can fail-closed by matching
    it against the `assumed` dependency lists from `check_axioms`. (A rogue `axiom` declared OUTSIDE
    this module is not in `declared_assumptions`, so `check_axioms` already taints anything leaning on
    it — this checker guards the in-module declarations.)"""
    mod = assumptions_module(deps)
    if not mod:
        return []
    text = tools.read_out(deps, mod)
    if text.startswith("ERROR:"):
        return []
    targets = target_defs(deps, translation)
    violations = []
    # One walker yields (qualified name, statement block) already paired — no positional zip that a
    # duplicate bare axiom name across namespaces could misalign.
    for qual, block in _qualified_decls(text, r"axiom"):
        hit = sorted(set(referenced_defs(block, translation)) & targets)
        if hit:
            violations.append({
                "axiom": qual, "targets": hit,
                "reason": (f"axiom `{qual}` references target function(s) {hit} — the trusted base "
                           f"may not contain a property of the code under verification (that would "
                           f"relax a GOAL); prove it, do not assume it")})
    return violations


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
    if not rmodule or not smodule:
        return []
    # One checker does both gates: a type-tie `example : False := <ref> <orig>` per refutation (forces
    # the negation to the EXACT statement) plus a `#print axioms` on each (purity — no `sorryAx`).
    ties = "\n".join(f"example : False := {ref_q} {orig_q}" for _, ref_q, orig_q in checks)
    prints = "\n".join(f"#print axioms {ref_q}" for _, ref_q, _ in checks)
    body = f"import {smodule}\nimport {rmodule}\n\n{ties}\n\n{prints}\n"
    code, text = _run_lean_checker(deps, body, "_refutation_check.lean")
    if code != 0 or "error:" in text:
        log.warning("verify_refutations: type-tie/checker did not compile — no refutation honored "
                    "(a refutation must prove ¬ the EXACT statement). Lean tail:\n%s", text[-1200:])
        return []
    axset = _parse_axiom_verdicts(text)
    refuted = [target for target, ref_q, _ in checks
               if ref_q in axset and "sorryAx" not in axset[ref_q]]
    if refuted:
        log.info("verify_refutations: %d theorem(s) mechanically REFUTED (false as stated): %s",
                 len(refuted), refuted)
    return refuted


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
    """Run `lake build` over the whole Lean project (all campaign spec modules) and record it."""
    result = check_lean(deps, campaign_spec(deps))
    deps.progress["lean_build"] = result
    deps.progress["build_seq"] = deps.progress.get("build_seq", 0) + 1
    checkpoint.snapshot(deps)
    return result


def sorry_count(deps: AgentDeps) -> int:
    """Number of theorems in the implementation spec still bodied by `sorry` — the genuinely-open
    obligations, and PROVE's progress metric. Counts per-theorem (not raw text occurrences), so a
    stray `sorry` in a header/proof comment is not miscounted. Returns -1 if the file can't be
    read, so the caller never mistakes it for done."""
    content = tools.read_out(deps, campaign_spec(deps))
    return -1 if content.startswith("ERROR:") else len(sorry_bodied_theorems(content))


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
