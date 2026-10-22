"""Regression tests for `_DECL_PREFIX` — declaration recognition in the regex helpers.

`_DECL_PREFIX` lets a declaration matcher see a keyword that is not the first token on its line
(`@[progress] theorem …`, `private theorem …`, and Aeneas's `@[global_simps, irreducible] def …`).
Two helpers rely on it and would silently drop a declaration if it regressed:

  • `_theorem_names` / `_theorem_qualified_names` — the theorem enumeration `check_axioms`,
    `impl_references` and the spec gate zip; a missed theorem is silently unchecked.
  • `_def_blocks` — the def scan behind `target_defs` (the legitimacy gate's whole-crate mode) and
    the hole count. Aeneas emits crate constants as `@[global_simps, irreducible] def <Crate>.CONST …`.
"""
import pytest

from lusterna.lean import _def_blocks, _theorem_names

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
def test_theorem_names_finds_every_declaration_form(name, form):
    spec = _module(form)
    assert name in _theorem_names(spec), f"{name}: not enumerated — would be silently unchecked"


def test_attributed_defs_are_found_and_bound():
    blocks = _def_blocks(TRANSLATION)
    assert list(blocks) == ["transfer", "Pubkey.ZERO", "widget", "after"]
    # An attributed def must also CLOSE the previous block, or it is swallowed into it.
    assert blocks["transfer"] == "def transfer (x : Nat) : Result Nat := ok x"
    assert blocks["Pubkey.ZERO"].startswith("@[global_simps, irreducible] def Pubkey.ZERO")
    assert blocks["widget"] == "@[reducible]\ndef widget (x : Nat) : Nat := x"
