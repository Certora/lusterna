"""Regression tests for `_DECL_PREFIX` — declaration recognition outside `stub_proofs`.

`stub_proofs` has its own suite (test_stub_proofs.py); these cover the two other places the
keyword-must-be-first-token rule silently dropped a declaration:

  • `theorem_statement`, which `_verifies_impl` calls to decide whether an established theorem
    references the implementation. Returning '' for `@[progress] theorem …` does not error — it
    just moves that theorem from the report's headline "verifies the implementation" count into
    "abstract helper", which is the wrong answer stated confidently.

  • `_def_blocks`, the def scan behind `referenced_defs`, `target_defs` (the legitimacy gate's
    whole-crate mode) and the hole count. Aeneas emits crate constants as
    `@[global_simps, irreducible] def <Crate>.CONST : … := …`.
"""
import pytest

from lusterna.lean import _def_blocks, _theorem_names, referenced_defs, theorem_statement

TRANSLATION = """def transfer (x : Nat) : Result Nat := ok x
@[global_simps, irreducible] def Pubkey.ZERO : Pubkey := 0#u64
@[reducible]
def widget (x : Nat) : Nat := x
def after (x : Nat) : Nat := x
"""

DECL_FORMS = [
    ("plain", "theorem plain (x : Nat) : transfer x = ok x"),
    ("as_lemma", "lemma as_lemma (x : Nat) : transfer x = ok x"),
    ("attributed", "@[progress] theorem attributed (x : Nat) : transfer x = ok x"),
    ("private_thm", "private theorem private_thm (x : Nat) : transfer x = ok x"),
    ("stacked", "@[simp] private theorem stacked (x : Nat) : transfer x = ok x"),
]


def _module(body: str) -> str:
    return f"import Aeneas\nnamespace S\n{body} := by sorry\nend S\n"


@pytest.mark.parametrize("name,form", DECL_FORMS)
def test_theorem_statement_finds_every_declaration_form(name, form):
    spec = _module(form)
    assert name in _theorem_names(spec), f"{name}: not even enumerated"
    stmt = theorem_statement(spec, name)
    assert stmt, f"{name}: enumerated but has no statement — counts as an abstract helper"
    assert "sorry" not in stmt, f"{name}: the proof leaked into the statement"
    assert referenced_defs(stmt, TRANSLATION) == ["transfer"]


def test_attributed_defs_are_found_and_bound():
    blocks = _def_blocks(TRANSLATION)
    assert list(blocks) == ["transfer", "Pubkey.ZERO", "widget", "after"]
    # An attributed def must also CLOSE the previous block, or it is swallowed into it.
    assert blocks["transfer"] == "def transfer (x : Nat) : Result Nat := ok x"
    assert blocks["Pubkey.ZERO"].startswith("@[global_simps, irreducible] def Pubkey.ZERO")
    assert blocks["widget"] == "@[reducible]\ndef widget (x : Nat) : Nat := x"


def test_theorem_referencing_only_an_attributed_def_counts():
    """The failure this pairs with: a theorem about a crate CONSTANT was invisible on both
    sides at once — the constant missing from the def scan, the theorem missing from the
    statement scan."""
    spec = _module("@[progress] theorem zero_is_zero : Pubkey.ZERO = 0#u64")
    stmt = theorem_statement(spec, "zero_is_zero")
    assert referenced_defs(stmt, TRANSLATION) == ["Pubkey.ZERO"]
