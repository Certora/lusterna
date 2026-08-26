"""Load bundled Aeneas documentation appended to the relevant stage briefings.

Files live under lusterna/docs/ and are committed to the repo so no network call is needed at
runtime. They are read once at module import and exposed as module-level strings (FOR_TRANSLATE,
FOR_FORMALISE, FOR_PROVE, FOR_SPEC_JUDGE) that briefings.py concatenates onto the corresponding
stage prompt.
"""
from pathlib import Path

_DOCS = Path(__file__).parent / "docs"


def _read(*parts: str) -> str:
    p = _DOCS.joinpath(*parts)
    if not p.exists():
        return ""
    return p.read_text()


def _section(title: str, content: str) -> str:
    if not content:
        return ""
    return f"\n\n{'─' * 60}\n## {title}\n{'─' * 60}\n\n{content}"


# ── curated subsets ───────────────────────────────────────────────────────────

# TRANSLATE drives Charon+Aeneas at the shell; it gets the translatability playbook — the measured
# verdict table (what translates / opaques / holes / rejects), the modelable-stdlib line, the
# behaviour-preserving recipes, and the charon/aeneas mechanics — so it recognises-and-applies
# instead of re-discovering Aeneas's fragment every run.
FOR_TRANSLATE: str = _section("Aeneas Translatability Playbook",
                              _read("skills", "aeneas-translate.md"))

# FORMALISE writes theorem STATEMENTS only (structured output — it cannot emit proofs), so
# it gets JUST the Aeneas translation semantics it needs to reference the Result monad,
# machine ints, casts, etc. correctly. No tactic/proof docs: they are irrelevant to
# statement-writing, and their lean-lsp "PREREQUISITE" guidance does not apply to a
# tool-less structured stage — keeping them out avoids nudging FORMALISE toward proving.
FOR_FORMALISE: str = _section("Aeneas Lean Core (translation semantics)",
                              _read("skills", "aeneas-lean-core.md"))

# PROVE proves against the `lake build` oracle (check_lean surfaces the real diagnostics), so
# it gets the Aeneas translation semantics, the tactic/pattern references, and the prose proof
# strategies — no interactive-LSP skill (that tooling was removed from PROVE).
FOR_PROVE: str = (
    FOR_FORMALISE
    + _section("Aeneas Tactics Quick-Reference",
               _read("skills", "aeneas-tactics-quickref.md"))
    + _section("Proof Patterns",
               _read("skills", "proof-patterns.md"))
    + _section("Proof Strategies",
               _read("prose", "proof-strategies.md"))
    + _section("Tactics Reference",
               _read("prose", "tactics-reference.md"))
)

# SPEC-JUDGE alone gets the mechanical-checks tool doc: what the checks catch, the exact
# invocation, worked examples of what they do and don't flag. It is a TOOL the judge may run, not
# part of its mandatory checklist — the doc says so explicitly, and the briefing must not
# contradict that. The spec-WRITING stages (FORMALISE, PROVE) get the underlying RULES instead,
# in aeneas-lean-core.md, phrased as how to write a statement rather than how to run a checker.
FOR_SPEC_JUDGE: str = _section("Mechanical Spec Checks (a tool, not a gate)",
                               _read("skills", "mechanical-checks.md"))
