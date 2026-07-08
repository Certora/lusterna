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

# For _formalise and _prove: core Lean translation semantics + tactics
FOR_FORMALISE: str = (
    _section("Aeneas Lean Core (translation semantics)",
             _read("skills", "aeneas-lean-core.md"))
    + _section("Aeneas Tactics Quick-Reference",
               _read("skills", "aeneas-tactics-quickref.md"))
    + _section("Proof Patterns",
               _read("skills", "proof-patterns.md"))
)

# For _prove: everything formalise gets plus proof strategies
FOR_PROVE: str = (
    FOR_FORMALISE
    + _section("Proof Strategies",
               _read("prose", "proof-strategies.md"))
    + _section("Tactics Reference",
               _read("prose", "tactics-reference.md"))
)
