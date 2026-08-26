"""Regression tests for `stub_proofs` — the TRUSTED no-smuggle gate.

`stub_proofs` runs on FORMALISE's module before acceptance so every theorem body is `sorry` and only
PROVE can earn a proof. It once required the `theorem`/`lemma` keyword to be the FIRST token on the
line, so `@[progress] theorem foo … := by <real proof>` was left untouched and the proof survived the
gate — while `_theorem_names`/`sorry_bodied_theorems` did not count it as an open obligation either.
`@[progress]` spec lemmas are exactly what the PROVE briefing asks the agent to write, so this was
reachable, not theoretical.

Two properties are tested for every declaration form: the proof is REMOVED, and the declaration is
NOT LOST. The second matters because `decl` (which opens a block) and `newtop` (which closes it) must
share the modifier vocabulary — when only one of them knew a modifier, the declaration was swallowed
into the previous theorem's block and dropped from the output entirely.
"""
import pytest

from lusterna.lean import _theorem_names, sorry_bodied_theorems, stub_proofs

FORMS = [
    "theorem plain (x : Nat) : x = x",
    "lemma plain_lemma (x : Nat) : x = x",
    "@[progress] theorem attr_same_line (x : Nat) : x = x",
    "@[simp, grind =] lemma attr_with_args (x : Nat) : x = x",
    "private theorem priv (x : Nat) : x = x",
    "protected lemma prot (x : Nat) : x = x",
    "nonrec theorem nonrec_thm (x : Nat) : x = x",
    "@[simp] private noncomputable theorem stacked (x : Nat) : x = x",
]
NAMES = ["plain", "plain_lemma", "attr_same_line", "attr_with_args", "priv", "prot",
         "nonrec_thm", "stacked"]


def _module(bodies: str) -> str:
    return f"import Aeneas\nnamespace T\n{bodies}\nend T\n"


@pytest.mark.parametrize("form,name", list(zip(FORMS, NAMES)))
def test_each_form_is_stubbed_in_isolation(form, name):
    out = stub_proofs(_module(f"{form} := by rfl"))
    assert "rfl" not in out, f"{name}: PROOF SURVIVED the no-smuggle gate"
    assert name in _theorem_names(out), f"{name}: declaration was LOST"
    assert name in sorry_bodied_theorems(out)


def test_all_forms_together_lose_nothing():
    """The adjacency case: every form back-to-back, so each one's block must be closed by the next
    one's opening line. A modifier known to `decl` but not to `newtop` silently DELETES a theorem
    here, which is worse than failing to stub it."""
    out = stub_proofs(_module("\n".join(f"{f} := by rfl" for f in FORMS)))
    assert _theorem_names(out) == NAMES
    assert "rfl" not in out
    assert sorted(sorry_bodied_theorems(out)) == sorted(NAMES)


def test_multiline_proof_body_is_removed():
    out = stub_proofs(_module("@[progress] theorem multi (x : Nat) : x = x := by\n  simp\n  rfl"))
    assert "simp" not in out and "rfl" not in out
    assert "multi" in _theorem_names(out)


def test_statement_with_record_update_is_preserved():
    """`_proposition_only`'s hazard, in `stub_proofs`' shape: a `:=` nested inside a statement must
    not be mistaken for the proof. Documented as a known limitation of the naive first-`:=` split —
    pinned here so a future change to either splitter is a deliberate one."""
    out = stub_proofs(_module("theorem rec_upd (s : S) : ({ s with n := 0 } : S).n = 0 := by rfl"))
    assert "rfl" not in out
    assert "rec_upd" in _theorem_names(out)


def test_defs_and_axioms_are_untouched():
    src = _module("def helper (x : Nat) : Nat := x + 1\naxiom trusted : True\n"
                  "@[progress] theorem t (x : Nat) : x = x := by rfl")
    out = stub_proofs(src)
    assert "def helper (x : Nat) : Nat := x + 1" in out
    assert "axiom trusted : True" in out
    assert "rfl" not in out
