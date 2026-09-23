"""Unit tests for the statement-drift detector (`lean._classify_drift`, `dump_statement_types`,
`check_statement_drift`).

The detector answers: did PROVE alter or drop a theorem statement the FORMALISE spec fixed? The
STATEMENT understanding is done in Lean — `dumpStatementTypes` emits a structural hash of each
theorem's elaborated TYPE — so these tests exercise the Python that DIFFS those Lean-produced hashes,
never any regex over Lean source. The Lean driver is stubbed with canned `LUSTERNA_CHECK` records
(the same shape `_parse_check_records` reads), exactly as the other gate tests stub theirs.
"""
from lusterna import lean
from lusterna.schemas import AgentDeps


# ── _classify_drift: the pure diff over Lean-produced fingerprints ─────────────────────────────────

def test_unchanged_statement_is_stable():
    old = {"C.foo": "h1", "C.bar": "h2"}
    res = lean._classify_drift(old, dict(old), refuted=set())
    assert res["stable"] == ["C.foo", "C.bar"]
    assert res["unaccounted"] == [] and res["accounted"] == []


def test_changed_statement_without_witness_is_unaccounted():
    res = lean._classify_drift({"C.foo": "h1"}, {"C.foo": "DIFFERENT"}, refuted=set())
    assert res["unaccounted"] == ["C.foo"] and res["stable"] == []


def test_removed_statement_without_witness_is_unaccounted():
    """Changed and removed collapse into the one `unaccounted` bucket — no consumer distinguishes them."""
    res = lean._classify_drift({"C.foo": "h1"}, {}, refuted=set())
    assert res["unaccounted"] == ["C.foo"] and res["accounted"] == []


def test_changed_statement_with_refutation_is_accounted():
    """A statement legitimately changes when it is found false and refuted (the P5 path): a
    `<name>__refuted` witness (short name in *refuted*) accounts for the change, not flagged."""
    res = lean._classify_drift({"C.foo": "h1"}, {"C.foo": "DIFFERENT"}, refuted={"foo"})
    assert res["accounted"] == ["C.foo"] and res["unaccounted"] == []


def test_removed_statement_with_refutation_is_accounted():
    res = lean._classify_drift({"C.foo": "h1"}, {}, refuted={"foo"})
    assert res["accounted"] == ["C.foo"] and res["unaccounted"] == []


def test_added_lemma_is_not_reported():
    """PROVE adds supporting lemmas; those exist only in *new* and must never be flagged."""
    old = {"C.foo": "h1"}
    new = {"C.foo": "h1", "C.helper": "h9", "C.helper2": "h8"}
    res = lean._classify_drift(old, new, refuted=set())
    assert res["stable"] == ["C.foo"]
    assert res["unaccounted"] == [] and res["accounted"] == []


def test_refutation_matches_on_short_name_under_namespace():
    """`refuted` carries short written names (from verify_refutations); a namespaced theorem must
    match on its last component."""
    res = lean._classify_drift({"ValueConservation.p5": "h1"}, {}, refuted={"p5"})
    assert res["accounted"] == ["ValueConservation.p5"]


# ── dump_statement_types: parsing the Lean driver output ───────────────────────────────────────────

def _deps():
    return AgentDeps(container_id="c", repo_path=".", session_id="s", design_doc="")


def test_dump_parses_statement_type_records(monkeypatch):
    monkeypatch.setattr(lean, "_crate_stem", lambda deps: "Crate")
    driver = (
        'LUSTERNA_CHECK {"check": "statement_type", "theorem": "C.foo", "hash": "111", "type": "P"}\n'
        'LUSTERNA_CHECK {"check": "statement_type", "theorem": "C.bar", "hash": "222", "type": "Q"}\n'
        'LUSTERNA_CHECK {"check": "other", "theorem": "C.noise", "hash": "999"}\n'
        "LUSTERNA_GATE_DONE\n"
    )
    monkeypatch.setattr(lean, "_run_lean_checker", lambda deps, body, rel: (0, driver))
    got = lean.dump_statement_types(_deps(), "Crate.Spec.C")
    assert got == {"C.foo": "111", "C.bar": "222"}   # only statement_type records, hash extracted


def test_dump_returns_empty_when_driver_did_not_finish(monkeypatch):
    """No sentinel ⇒ the driver did not run to completion ⇒ conservative empty (caller treats as
    unable-to-check, never a silent pass)."""
    monkeypatch.setattr(lean, "_crate_stem", lambda deps: "Crate")
    monkeypatch.setattr(lean, "_run_lean_checker", lambda deps, body, rel: (1, "boom, no sentinel"))
    assert lean.dump_statement_types(_deps(), "Crate.Spec.C") == {}


# ── check_statement_drift: FORMALISE baseline vs a fresh dump ──────────────────────────────────────

def test_check_uses_progress_baseline_and_flags_unaccounted(monkeypatch):
    deps = _deps()
    deps.progress["formalise_types"] = {"C.foo": "h1", "C.bar": "h2"}
    monkeypatch.setattr(lean, "campaign_spec_module", lambda deps: "Crate.Spec.C")
    # bar's statement changed since FORMALISE, no refutation for it
    monkeypatch.setattr(lean, "dump_statement_types", lambda deps, mod: {"C.foo": "h1", "C.bar": "CHANGED"})
    res = lean.check_statement_drift(deps, refuted=[])
    assert res["stable"] == ["C.foo"] and res["unaccounted"] == ["C.bar"] and res["accounted"] == []


def test_all_empty_when_no_baseline(monkeypatch):
    """No FORMALISE fingerprint captured ⇒ all buckets empty (the could-not-run signal the verdict
    surfaces as "not verified" — never a fabricated clean pass)."""
    deps = _deps()
    assert lean.check_statement_drift(deps, refuted=[]) == {"stable": [], "accounted": [], "unaccounted": []}


def test_all_empty_when_fresh_dump_unavailable(monkeypatch):
    deps = _deps()
    deps.progress["formalise_types"] = {"C.foo": "h1"}
    monkeypatch.setattr(lean, "campaign_spec_module", lambda deps: "Crate.Spec.C")
    monkeypatch.setattr(lean, "dump_statement_types", lambda deps, mod: {})
    assert lean.check_statement_drift(deps, refuted=[]) == {"stable": [], "accounted": [], "unaccounted": []}
