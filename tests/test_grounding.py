"""Unit tests for the POST-PROVE grounding rung (`lean.check_grounding`).

The rung re-times the FORMALISE anchor check to the ESTABLISHED set: an anchor is `grounded` only if a
theorem that HOLDS references it in a measurement position. These tests pin the Python side without a
container — the name re-scoping (only established theorems reach the Lean driver), the record parsing,
and the CONSERVATIVE fallbacks (a driver that cannot run, or nothing established, leaves every anchor
ungrounded — never silently grounded). The Lean `checkAnchorReference` itself is exercised by
tests/checklean and reused here unchanged.
"""
import pytest

from lusterna import lean
from lusterna.schemas import AgentDeps


# Two theorems: a bridge that references the anchor `g`, and an invariant that does not.
SPEC = """import Foo
namespace Foo.Spec
@[lusterna]
theorem bridge (l t) (h : g l = ok t) : t.val = proj l := by sorry
@[lusterna]
theorem inv (r) : P r := by sorry
end Foo.Spec
"""


def _deps(anchors=("crate::g",)):
    d = AgentDeps(container_id="c", repo_path=".", session_id="s", design_doc="")
    d.progress["anchor_functions"] = list(anchors)
    return d


@pytest.fixture
def stub(monkeypatch):
    """Drive `check_grounding` without Lean: fake the module list, crate stem, spec read, and the
    Lean run. `driver_out` is the checker's stdout; `capture` collects the driver body for scoping
    assertions."""
    capture: dict = {}

    def install(driver_out):
        monkeypatch.setattr(lean, "spec_modules", lambda deps: ["lean/Foo/Spec/Camp.lean"])
        monkeypatch.setattr(lean, "_crate_stem", lambda deps: "Foo")
        monkeypatch.setattr(lean.tools, "read_out", lambda deps, rel: SPEC)

        def fake_run(deps, body, rel):
            capture["body"] = body
            return (0, driver_out)
        monkeypatch.setattr(lean, "_run_lean_checker", fake_run)
        return capture
    return install


def _out(referenced: bool, anchor="g") -> str:
    val = "true" if referenced else "false"
    return (f'info: LUSTERNA_CHECK {{"check": "anchor_bridge", "anchor": "{anchor}", '
            f'"referenced": {val}}}\n{lean._GATE_DONE}\n')


def test_no_anchors_is_a_noop(monkeypatch):
    """No INFER anchors → nothing to ground; the Lean driver must never run (fail-safe)."""
    monkeypatch.setattr(lean, "_run_lean_checker",
                        lambda *a, **k: pytest.fail("driver ran with no anchors"))
    assert lean.check_grounding(_deps(anchors=[]), {"Camp::bridge"}) == {
        "anchors": [], "grounded": [], "ungrounded": []}


def test_referenced_by_established_theorem_is_grounded(stub):
    stub(_out(referenced=True))
    g = lean.check_grounding(_deps(), {"Camp::bridge"})
    assert g["grounded"] == ["g"] and g["ungrounded"] == [] and g["anchors"] == ["g"]


def test_not_referenced_is_ungrounded(stub):
    stub(_out(referenced=False))
    g = lean.check_grounding(_deps(), {"Camp::bridge"})
    assert g["ungrounded"] == ["g"] and g["grounded"] == []


def test_only_established_theorems_reach_the_driver(stub):
    """THE RE-SCOPING. `bridge` is established, `inv` is not; the driver must be handed the
    fully-qualified name of the established theorem ONLY — a bridge left unproven cannot ground an
    anchor precisely because its name never reaches `checkAnchorReference`."""
    cap = stub(_out(referenced=True))
    lean.check_grounding(_deps(), {"Camp::bridge"})   # inv is NOT clean
    assert "Foo.Spec.bridge" in cap["body"]
    assert "Foo.Spec.inv" not in cap["body"]


def test_nothing_established_is_ungrounded_without_running(monkeypatch):
    """An empty established set means no theorem can ground an anchor; short-circuit to ungrounded and
    do not invoke Lean."""
    monkeypatch.setattr(lean, "spec_modules", lambda deps: ["lean/Foo/Spec/Camp.lean"])
    monkeypatch.setattr(lean, "_crate_stem", lambda deps: "Foo")
    monkeypatch.setattr(lean.tools, "read_out", lambda deps, rel: SPEC)
    monkeypatch.setattr(lean, "_run_lean_checker",
                        lambda *a, **k: pytest.fail("driver ran with nothing established"))
    g = lean.check_grounding(_deps(), set())
    assert g["ungrounded"] == ["g"] and g["grounded"] == []


def test_driver_that_did_not_finish_is_conservatively_ungrounded(stub):
    """No sentinel = the driver died. Like `check_axioms`, that must read as ungrounded (loud and
    conservative), never as grounded on silence."""
    stub('info: LUSTERNA_CHECK {"check": "anchor_bridge", "anchor": "g", "referenced": true}\n')
    g = lean.check_grounding(_deps(), {"Camp::bridge"})
    assert g["ungrounded"] == ["g"] and g["grounded"] == []


def test_checker_error_is_conservatively_ungrounded(stub):
    stub("ERROR: lake env lean failed")
    g = lean.check_grounding(_deps(), {"Camp::bridge"})
    assert g["ungrounded"] == ["g"] and g["grounded"] == []
