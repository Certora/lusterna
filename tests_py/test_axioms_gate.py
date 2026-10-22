"""Regression tests for the AUTHORITATIVE gate `lean.check_axioms` after its move to MetaM
`collectAxioms`.

The classification (clean / assumed / tainted) now lives in Lean and arrives as
`LUSTERNA_CHECK {check:"axioms", …}` records; these tests pin the Python mapping WITHOUT Docker:
the record→verdict translation, the unresolved-theorem fallback (a theorem with no record is
tainted), and the fail-closed driver-failure path (no sentinel ⇒ every theorem tainted, never a
silent clean).
"""
from lusterna import lean
from lusterna.schemas import AgentDeps

SPEC = """import Foo.LusternaSchemas
namespace Foo.Spec
theorem a_clean (x : Nat) : x = x := by sorry
theorem b_assumed (x : Nat) : x = x := by sorry
theorem c_tainted (x : Nat) : x = x := by sorry
end Foo.Spec
"""


def _deps():
    return AgentDeps(container_id="c", repo_path=".", session_id="s", design_doc="")


def _stub(monkeypatch, lean_output: str):
    monkeypatch.setattr(lean.tools, "read_out", lambda *a, **k: SPEC)
    monkeypatch.setattr(lean, "declared_assumptions", lambda _d: {"Foo.Assumptions.ax1"})
    monkeypatch.setattr(lean, "_run_lean_checker", lambda *a, **k: (0, lean_output))


def _rec(name: str, status: str, used: list[str]) -> str:
    u = ", ".join(f'"{x}"' for x in used)
    return f'LUSTERNA_CHECK {{"check": "axioms", "theorem": "{name}", "status": "{status}", "used": [{u}]}}'


def test_three_way_classification(monkeypatch):
    out = "\n".join([
        _rec("Foo.Spec.a_clean", "clean", []),
        _rec("Foo.Spec.b_assumed", "assumed", ["Foo.Assumptions.ax1"]),
        _rec("Foo.Spec.c_tainted", "tainted", ["sorryAx"]),
        lean._GATE_DONE,
    ])
    _stub(monkeypatch, out)
    res = lean.check_axioms(_deps(), "lean/Foo/Spec.lean")
    assert res["clean"] == ["a_clean"]
    assert res["assumed"] == {"b_assumed": ["Foo.Assumptions.ax1"]}
    assert res["tainted"] == ["c_tainted"]


def test_theorem_without_a_record_is_tainted(monkeypatch):
    # b_assumed's record is missing — the driver never classified it, so it is unresolved → tainted,
    # never dropped or assumed clean.
    out = "\n".join([
        _rec("Foo.Spec.a_clean", "clean", []),
        _rec("Foo.Spec.c_tainted", "tainted", ["sorryAx"]),
        lean._GATE_DONE,
    ])
    _stub(monkeypatch, out)
    res = lean.check_axioms(_deps(), "lean/Foo/Spec.lean")
    assert res["clean"] == ["a_clean"]
    assert set(res["tainted"]) == {"b_assumed", "c_tainted"}
    assert res["assumed"] == {}


def test_driver_failure_taints_everything(monkeypatch):
    # No sentinel: the driver died, nothing was established — every theorem tainted, fail-closed.
    _stub(monkeypatch, _rec("Foo.Spec.a_clean", "clean", []))   # a record but NO _GATE_DONE
    res = lean.check_axioms(_deps(), "lean/Foo/Spec.lean")
    assert res["clean"] == [] and res["assumed"] == {}
    assert set(res["tainted"]) == {"a_clean", "b_assumed", "c_tainted"}


def test_empty_spec_is_empty(monkeypatch):
    monkeypatch.setattr(lean.tools, "read_out", lambda *a, **k: "namespace Foo.Spec\nend Foo.Spec\n")
    res = lean.check_axioms(_deps(), "lean/Foo/Spec.lean")
    assert res == {"clean": [], "assumed": {}, "tainted": [], "sorry": [], "raw": ""}
