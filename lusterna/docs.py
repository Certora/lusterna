"""Load bundled Aeneas documentation and inject into agent instructions.

Files live under lusterna/docs/ and are committed to the repo so no network
call is needed at runtime.  They are read once at module import and exposed
as module-level strings.

Usage in agent.py:
    from . import docs
    # append docs.FOR_FORMALISE to formalise agent instructions
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

# FORMALISE writes theorem STATEMENTS only (structured output — it cannot emit proofs), so
# it gets JUST the Aeneas translation semantics it needs to reference the Result monad,
# machine ints, casts, etc. correctly. No tactic/proof docs: they are irrelevant to
# statement-writing, and their lean-lsp "PREREQUISITE" guidance does not apply to a
# tool-less structured stage — keeping them out avoids nudging FORMALISE toward proving.
FOR_FORMALISE: str = _section("Aeneas Lean Core (translation semantics)",
                              _read("skills", "aeneas-lean-core.md"))

# PROVE gets the lean-lsp-mcp interactive-proof skill FIRST (the Aeneas skill files list it
# as a PREREQUISITE), then translation semantics, the tactic/pattern references, and the
# prose proof strategies.
FOR_PROVE: str = (
    _section("Lean LSP MCP — interactive proof development (PREREQUISITE)",
             _read("skills", "lean-lsp-mcp.md"))
    + FOR_FORMALISE
    + _section("Aeneas Tactics Quick-Reference",
               _read("skills", "aeneas-tactics-quickref.md"))
    + _section("Proof Patterns",
               _read("skills", "proof-patterns.md"))
    + _section("Proof Strategies",
               _read("prose", "proof-strategies.md"))
    + _section("Tactics Reference",
               _read("prose", "tactics-reference.md"))
)
