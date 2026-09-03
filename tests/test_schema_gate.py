"""Regression tests for THE SPEC GATE (`lean.check_spec_gate`).

Both checks in `spec_checks.lean` block FORMALISE now — no stage interprets them. That is sound only
because each theorem DECLARES its family, which retires the shape rules' documented false positives
(a relational antecedent needs two executions, which the one checked shape forbids; and so on).

Two properties carry the weight here:

  • "could not check" is NEVER reported as "conforms". The gate reads a `LUSTERNA_GATE_DONE`
    sentinel for exactly the reason `check_axioms` treats an unresolved theorem as tainted: silence
    from a driver that died partway looks identical to silence from a clean run.
  • the failure message names the RULE. Without it FORMALISE gets "your spec is wrong" and no way to
    act, which defeats the point of gating on conformance rather than on judgment.
"""
from pathlib import Path

import pytest

from lusterna import config, lean, pipeline
from lusterna.schemas import AgentDeps


SPEC = """import Foo.LusternaSchemas
namespace Foo.Spec
@[lusterna]
theorem good (x : Nat) : x = x := by sorry
theorem bare (x : Nat) : x = x := by sorry
end Foo.Spec
"""

FINDING = ('x.lean:6:0: info: LUSTERNA_CHECK {"check": "schema_conformance", '
           '"theorem": "Foo.Spec.bare", "schema": "none", "rule": "no_schema_declared", '
           '"detail": "every spec theorem must declare a family"}')
TAINT = ('LUSTERNA_CHECK {"check": "assumed_postcondition", "theorem": "Foo.Spec.good", '
         '"fn": "f", "tainted": ["r"], "hypothesis": "h", "rule": "constrains tainted variable", '
         '"detail": "d", "schema": "checked", "is_conclusion": false}')
UNRELATED = 'LUSTERNA_CHECK {"check": "some_future_check", "theorem": "Foo.Spec.good"}'
SKIPPED = ('LUSTERNA_CHECK_SKIPPED {"check": "schema_conformance", '
           '"theorem": "Foo.Spec.good", "schema": "checked", "reason": "no targets given"}')


def _deps():
    return AgentDeps(container_id="c", repo_path=".", session_id="s", design_doc="")


@pytest.fixture
def stub(monkeypatch):
    """Drive `check_spec_gate` without Docker: fake the spec read and the Lean run."""
    def install(lean_output, spec=SPEC):
        monkeypatch.setattr(lean.tools, "read_out", lambda *a, **k: spec)
        monkeypatch.setattr(lean, "_run_lean_checker", lambda *a, **k: (0, lean_output))
        monkeypatch.setattr(lean, "_schema_targets", lambda _d: ["Foo.transfer"])
    return install


def test_conforming_spec_returns_no_failures(stub):
    """A spec WITH theorems, a driver that finished, and no findings — the only shape that may pass."""
    stub(f"{lean._GATE_DONE}\n")
    assert lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean") == []


def test_skipped_record_blocks(stub):
    """A SKIPPED record names a theorem that went UNCHECKED, while the finding list still looks
    complete. Dropping it would let "nothing was checked" reach the gate as "everything conforms" —
    the same rule tests/checklean/verify.py pins for the judge-facing path."""
    stub(f"{SKIPPED}\n{lean._GATE_DONE}\n")
    bad = lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean")
    assert [b["rule"] for b in bad] == ["could_not_check"]
    assert bad[0]["theorem"] == "Foo.Spec.good"


def test_empty_targets_block(monkeypatch):
    """With no targets the checker cannot identify an execution and skips every theorem. An
    unreadable translation must therefore BLOCK, not report conformance — the failure mode the
    whole-crate fallback in `_schema_targets` would otherwise introduce."""
    monkeypatch.setattr(lean.tools, "read_out", lambda *a, **k: SPEC)
    monkeypatch.setattr(lean, "_schema_targets", lambda _d: [])
    # the Lean run must never even be reached
    monkeypatch.setattr(lean, "_run_lean_checker",
                        lambda *a, **k: pytest.fail("Lean was run with no targets"))
    bad = lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean")
    assert [b["rule"] for b in bad] == ["could_not_check"]


def test_unreadable_translation_yields_no_targets(monkeypatch):
    """`_schema_targets` must return empty (so the caller blocks) rather than raising, when the
    translation cannot be read at all."""
    deps = _deps()
    deps.progress["target_patterns"] = []
    monkeypatch.setattr(lean, "translation_text", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert lean._schema_targets(deps) == []


def test_failure_is_parsed_with_its_rule(stub):
    stub(f"{FINDING}\n{lean._GATE_DONE}\n")
    bad = lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean")
    assert len(bad) == 1
    assert bad[0]["theorem"] == "Foo.Spec.bare"
    assert bad[0]["rule"] == "no_schema_declared"


def test_all_three_checks_block(stub):
    """The gate is the composition, so a taint finding blocks exactly as a conformance one does."""
    stub(f"{TAINT}\n{lean._GATE_DONE}\n")
    bad = lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean")
    assert [b["check"] for b in bad] == ["assumed_postcondition"]


def test_findings_from_unknown_checks_are_ignored(stub):
    """Every check shares one output token, so a finding from something outside the gate's own set
    must not silently become a rejection."""
    stub(f"{UNRELATED}\n{lean._GATE_DONE}\n")
    assert lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean") == []


def test_missing_sentinel_is_could_not_check_not_clean(stub):
    """The whole point of the sentinel. A driver that died prints no findings — identical to a clean
    run — so absence of the sentinel must BLOCK, never pass."""
    stub("error: unknown identifier 'checkSchemaConformance'\n")
    bad = lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean")
    assert [b["rule"] for b in bad] == ["could_not_check"]


def test_unreadable_spec_blocks(stub):
    stub(f"{lean._GATE_DONE}\n", spec="ERROR: no such file")
    assert [b["rule"] for b in lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean")] == ["spec_unreadable"]


def test_bad_spec_path_blocks(stub):
    stub(f"{lean._GATE_DONE}\n")
    assert [b["rule"] for b in lean.check_spec_gate(_deps(), "notlean/Spec.lean")] == ["bad_spec_path"]


def test_spec_with_no_theorems_is_vacuously_fine(stub):
    stub(f"{lean._GATE_DONE}\n", spec="import Foo\nnamespace Foo.Spec\nend Foo.Spec\n")
    assert lean.check_spec_gate(_deps(), "lean/Foo/Spec.lean") == []


def _matches(lean_name: str, forms: list[str]) -> bool:
    """The checker's own dotted-suffix rule (`nameHasSuffix` in spec_checks.lean)."""
    return any(lean_name == f or lean_name.endswith("." + f) for f in forms)


def test_targets_rewrite_rust_paths_to_dotted():
    deps = _deps()
    deps.progress["target_patterns"] = ["a::b::c", "transfer", ""]
    forms = lean._schema_targets(deps)
    assert "a.b.c" in forms and "transfer" in forms


def test_charon_wildcard_patterns_still_match_generated_lean():
    """THE REGRESSION. INFER emits Charon matchers, where `_` is a wildcard for the impl/type:
    `crate::foo::_::measure`. Aeneas generates
    `crate.foo.Bar.measure`, so a plain `::`→`.` rewrite produces
    `crate.foo._.measure` and matches NOTHING — every target lookup comes back empty,
    and the conformance check then reports `found 0` on every theorem. A real run stalled on
    exactly this, for 8 FORMALISE rounds."""
    deps = _deps()
    deps.progress["target_patterns"] = [
        "crate::foo::_::measure",
        "crate::state::reserve::_::deposit",
        "crate::approximate_compounded_interest",
    ]
    forms = lean._schema_targets(deps)
    for generated in ("crate.foo.Bar.measure",
                      "state.reserve.Reserve.deposit",
                      "approximate_compounded_interest"):
        assert _matches(generated, forms), f"{generated} unmatched by {forms}"
    # and the un-matchable literal must not be emitted at all
    assert not any("_" in f.split(".") for f in forms)


def test_wildcard_free_patterns_keep_their_full_path():
    """A pattern that names the type fully keeps the stricter dotted form, not just the tail."""
    deps = _deps()
    deps.progress["target_patterns"] = ["crate::state::reserve::Reserve::borrow"]
    forms = lean._schema_targets(deps)
    assert "state.reserve.Reserve.borrow" in forms and "borrow" in forms


def test_format_gate_findings_names_the_check_and_rule():
    """FORMALISE acts on this message unaided, so it must carry both names."""
    msg = pipeline._format_gate_findings(
        [{"check": "schema_conformance", "theorem": "Foo.Spec.bare", "schema": "checked",
          "rule": "execution_not_unique", "detail": "found 2"}])
    for expect in ("Foo.Spec.bare", "schema_conformance", "execution_not_unique", "checked", "found 2"):
        assert expect in msg, expect


def test_gate_is_unconditional():
    """There is no on/off knob. A gate an agent can discover is optional is not a gate — a real run
    had FORMALISE grep the harness for `config.SCHEMA_GATE`, find it defaulted off, and use that to
    justify ignoring the critique."""
    assert not hasattr(config, "SCHEMA_GATE")
    src = Path(pipeline.__file__).read_text()
    assert "if bad := lean.check_spec_gate(deps, impl):" in src
