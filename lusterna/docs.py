"""Load bundled Aeneas documentation appended to the relevant stage briefings.

Files live under lusterna/docs/ and are committed to the repo so no network call is needed at
runtime. They are read once at module import and exposed as module-level strings (FOR_TRANSLATE,
FOR_FORMALISE, FOR_PROVE, FOR_SPEC_GATE) that briefings.py concatenates onto the corresponding
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

# FORMALISE gets the spec-gate reference, because FORMALISE is who the gate BLOCKS. It is no longer
# a tool anyone invokes and interprets: the harness runs it, a finding is a hard rejection, and the
# critique names the check and the rule. So the stage that has to satisfy the rules is the stage that
# needs them written out — SPEC-JUDGE no longer runs the checks at all and is briefed on semantics.
FOR_SPEC_GATE: str = _section("The Mechanical Spec Gate (what FORMALISE must clear)",
                              _read("skills", "mechanical-checks.md"))


# ── platform docs ─────────────────────────────────────────────────────────────
# One rung MORE specific than the general skills (true for all Aeneas targets), one rung MORE general
# than a per-campaign instruction doc: semantics common to every program on an execution PLATFORM.
# Appended to a stage briefing when the target is detected to run on that platform
# (`pipeline._detect_platform`, keyed structurally off the runtime, not a framework). Add a platform
# by dropping a `docs/platforms/<name>.md` and a row here.
_PLATFORM_DOCS: dict[str, str] = {
    "solana": _section("Solana program semantics", _read("platforms", "solana.md")),
}


def platform_addendum(platform: str | None) -> str:
    """The platform doc for `platform` (from `deps.progress['platform']`), or '' for none/unknown —
    so a stage that always concatenates it is a no-op off-platform."""
    return _PLATFORM_DOCS.get(platform or "", "")
