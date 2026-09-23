#!/usr/bin/env python3
"""Verify arbitrary theorems against the mechanical checks in `LusternaChecks.lean`.

Two modes:

  python tests/checklean/verify.py
      Runs the regression fixture (tests/checklean/*.lean) and asserts the exact expected finding
      set, including each schema_conformance finding's `rule`. This is what CI should run.

  python tests/checklean/verify.py --file mytheorem.lean --targets Foo.bar,Foo.baz
                                   [--subjects Foo.transfer] [--deps a.lean b.lean]
      Builds an arbitrary .lean file (plus optional dependency files) as a throwaway crate and
      prints whatever the checks find on the named theorems (`--subjects` adds the target-aware checks, which must
      be told which functions are under test) — raw output, no pass/fail
      judgment, since correctness here is exactly what a human or agent is meant to decide. This
      is the literal "verify an arbitrary theorem" tool: point it at any snippet and any theorem
      name and see what fires.

Both modes emit the EXACT driver documented for agents in docs/skills/mechanical-checks.md — same
imports, same `maxRecDepth`, same three `check*` calls per target, same bare `LUSTERNA_CHECK_DONE`
sentinel, read the same way ("the driver finished, so silence means clean"). Nothing here bypasses
that path for a shortcut answer, so a documented invocation that does not work fails here first.

Requires Docker and the `lusterna-toolchain:latest` image.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lusterna import container, lean, tools                      # noqa: E402
from lusterna.schemas import AgentDeps                            # noqa: E402

HERE = Path(__file__).parent
CONTAINER_NAME = "lusterna-checklean-verify"

_CHECK_RE = re.compile(r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK\s+(\{.*\})\s*$")
# The JSON payload is OPTIONAL: the invocation documented for agents prints a bare
# `LUSTERNA_CHECK_DONE` sentinel, and this harness must accept exactly what that produces.
_DONE_RE = re.compile(r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK_DONE(?:\s+(\{.*\}))?\s*$")
_SKIP_RE = re.compile(r"(?m)^\s*(?:\S+:\d+:\d+:\s*)?(?:info:\s*)?LUSTERNA_CHECK_SKIPPED\s+(\{.*\})\s*$")

# Every finding the fixture (Schemas.lean) must produce, keyed by (check, theorem). Under the TRIPLE
# checked-schema every finding is a `schema_conformance` (one per broken rule) plus the standalone
# `assumption_legitimacy` from Legit.lean; the equational-era taint fixpoint is gone.
EXPECTED = {
    ("schema_conformance", "Probe.Schemas.s5_not_a_triple"),
    ("schema_conformance", "Probe.Schemas.s6_triple_not_over_target"),
    ("schema_conformance", "Probe.Schemas.s7_ignores_output"),
    ("schema_conformance", "Probe.Schemas.s8_post_measurement"),
    ("schema_conformance", "Probe.Schemas.s9_vacuous_direct"),
    ("schema_conformance", "Probe.Schemas.s10_vacuous_predicate"),
    ("schema_conformance", "Probe.Schemas.s11_vacuous_conj"),
    ("schema_conformance", "Probe.Schemas.s12_vacuous_structure"),
    ("schema_conformance", "Probe.Schemas.s13_unannotated"),
    ("schema_conformance", "Probe.Schemas.s14_double"),
    ("assumption_legitimacy", "Probe.Legit.deposit_monotone"),
}

# Each conformance finding's `rule` — a finding for the wrong reason is an unactionable retry message.
EXPECTED_SCHEMA_RULES = {
    "Probe.Schemas.s5_not_a_triple":           "not_a_target_triple",
    "Probe.Schemas.s6_triple_not_over_target": "triple_not_over_target",
    "Probe.Schemas.s7_ignores_output":         "conclusion_ignores_output",
    "Probe.Schemas.s8_post_measurement":       "claim_not_failsafe",
    "Probe.Schemas.s9_vacuous_direct":         "vacuous_claim",
    "Probe.Schemas.s10_vacuous_predicate":     "vacuous_claim",
    "Probe.Schemas.s11_vacuous_conj":          "vacuous_claim",
    "Probe.Schemas.s12_vacuous_structure":     "vacuous_claim",
    "Probe.Schemas.s13_unannotated":           "no_schema_declared",
    "Probe.Schemas.s14_double":                "multiple_schemas_declared",
}

EXPECTED_FINDING_COUNT = 11   # 10 schema_conformance + 1 assumption_legitimacy

# The checked-property fixtures — (theorem, targets), run through `checkSpecGate`. Every s-theorem is
# listed (clean controls too) so a check that silently stopped firing cannot still "pass". The only
# target is `Probe.f`; `s6` runs `Probe.Schemas.k` (not a target) -> `triple_not_over_target`.
SCHEMA_THEOREMS = [(f"Probe.Schemas.{n}", ["Probe.f"]) for n in [
    "s1_conforms", "s2_conditional_clean", "s3_lemma",
    "s5_not_a_triple", "s6_triple_not_over_target", "s7_ignores_output", "s8_post_measurement",
    "s9_vacuous_direct", "s10_vacuous_predicate", "s11_vacuous_conj", "s12_vacuous_structure",
    "s13_unannotated", "s14_double", "g_lemma_bridge",
]]

# `assumption_legitimacy` — (axioms to check, target patterns). See Legit.lean.
LEGIT_CHECK = (["Probe.Legit.limbMul_spec", "Probe.Legit.deposit_monotone"], ["deposit"])

# `checkDefAxioms` — the TRANSLATE-time opaque-footprint disclosure. See DefAx.lean.
EXPECTED_DEF_AXIOMS = {
    "Probe.DefAx.cleanDef":      [],
    "Probe.DefAx.leakyDirect":   ["Probe.DefAx.Op"],
    "Probe.DefAx.leakyIndirect": ["Probe.DefAx.Op"],
}


def sh(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(args)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def parse_findings(out: str) -> tuple[list[dict], list[dict], dict | None]:
    """Parse `LUSTERNA_CHECK` / `LUSTERNA_CHECK_SKIPPED` / `LUSTERNA_CHECK_DONE` lines. Mirrors
    exactly what an agent (or SPEC-JUDGE) sees reading the same `lake env lean` output — no hidden
    extra parsing power."""
    findings = [json.loads(m.group(1)) for m in _CHECK_RE.finditer(out)]
    skipped = [json.loads(m.group(1)) for m in _SKIP_RE.finditer(out)]
    done = _DONE_RE.search(out)
    if not done:
        return findings, skipped, None
    return findings, skipped, (json.loads(done.group(1)) if done.group(1) else {})


def run_checks(cid: str, lean_dir: str, import_lines: list[str],
               schema: list[tuple[str, list[str]]] | None = None,
               legitimacy: tuple[list[str], list[str]] | None = None,
               ) -> tuple[list[dict], list[dict], dict | None, str]:
    """Write a tiny driver that imports the checker plus the target module(s), call the `check*`
    functions, run it with `lake env lean`. *schema* pairs a theorem with the TARGET functions
    `checkSpecGate` needs told. Returns (findings, skipped, done-or-None, raw); `done is None` means
    the driver never finished (a real compile error) -- treat as "could not check", never clean."""
    body_lines = import_lines + [
        "open Lusterna.Checks",
        "set_option maxRecDepth 4000 in",
        "#eval show Lean.Meta.MetaM Unit from do",
    ]
    for t, tgts in (schema or []):
        arr = ", ".join("`" + g for g in tgts)
        body_lines.append(f"  let _ ← checkSpecGate `{t} #[{arr}]")
    if legitimacy is not None:
        axioms, tgts = legitimacy
        aarr = ", ".join("`" + a for a in axioms)
        tarr = ", ".join("`" + g for g in tgts)
        body_lines.append(f"  checkAssumptionLegitimacy #[{aarr}] #[{tarr}]")
    body_lines.append('  IO.println "LUSTERNA_CHECK_DONE"')
    body = "\n".join(body_lines) + "\n"
    rel = "_verify_driver.lean"
    assert not tools.write_out(AgentDeps(container_id=cid, repo_path=".", session_id="v",
                                         design_doc=""), f"lean/{rel}", body).startswith("ERROR:")
    code, out, err = container.exec_in(cid, ["lake", "env", "lean", rel], workdir=lean_dir, timeout=300)
    combined = out + "\n" + err
    findings, skipped, done = parse_findings(combined)
    if code != 0 and done is None:
        return [], [], None, combined
    return findings, skipped, done, combined


def run_fixture() -> int:
    sh("docker", "rm", "-f", CONTAINER_NAME, check=False)
    cid = sh("docker", "run", "--rm", "--detach", "--name", CONTAINER_NAME,
             "lusterna-toolchain:latest", "sleep", "infinity").strip()
    print(f"container {cid[:12]}")
    try:
        deps = AgentDeps(container_id=cid, repo_path=HERE, session_id="fixture",
                         design_doc="", campaign="Fixture")
        lean_dir = f"{container.OUT_IN}/lean"
        sh("docker", "exec", cid, "mkdir", "-p", f"{lean_dir}/Probe")
        for src, dst in [(HERE / "Probe.lean", "lean/Probe.lean"),
                         (HERE / "Schemas.lean", "lean/Probe/Schemas.lean"),
                         (HERE / "DefAx.lean", "lean/Probe/DefAx.lean"),
                         (HERE / "Legit.lean", "lean/Probe/Legit.lean")]:
            assert not tools.write_out(deps, dst, src.read_text()).startswith("ERROR:")
        lean.setup_lake(deps)   # writes the lakefile AND LusternaChecks.lean, exactly as in prod

        print("lake build ...")
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            return sys.exit(f"fixture did not build:\n{out}\n{err}")

        findings, skipped, done, _ = run_checks(
            cid, lean_dir,
            ["import Probe.LusternaChecks", "import Probe.Schemas", "import Probe.Legit"],
            schema=SCHEMA_THEOREMS, legitimacy=LEGIT_CHECK)
        if done is None:
            return sys.exit("the driver file never completed -- see raw output above")

        got = {(f["check"], f.get("theorem") or f.get("axiom", "?")) for f in findings}
        print(f"\n{len(findings)} finding(s) (expected {EXPECTED_FINDING_COUNT}):")
        for f in sorted(findings, key=lambda d: (d["check"], str(d.get("theorem") or d.get("axiom")))):
            who = f.get("theorem") or f.get("axiom")
            print(f"  [{f['check']}] {who}: {f.get('rule') or f.get('reason')}")
        missing = EXPECTED - got
        spurious = got - EXPECTED
        for label, d in [("MISSED (false negative)", sorted(missing)),
                         ("SPURIOUS (false positive)", sorted(spurious))]:
            if d:
                print(f"\n{label}: {d}")
        rules = {f["theorem"]: f.get("rule") for f in findings if f["check"] == "schema_conformance"}
        rule_bad = {k: (rules.get(k), v) for k, v in EXPECTED_SCHEMA_RULES.items() if rules.get(k) != v}
        if rule_bad:
            print("\nWRONG RULE (theorem: got, expected):")
            for k, (g, e) in sorted(rule_bad.items()):
                print(f"  {k}: {g!r} != {e!r}")
        count_ok = len(findings) == EXPECTED_FINDING_COUNT
        if not count_ok:
            print(f"\nCOUNT MISMATCH: got {len(findings)}, expected {EXPECTED_FINDING_COUNT}")

        # -- checkDefAxioms: the TRANSLATE-time opaque-footprint disclosure --
        da_pats = ", ".join("`" + d.rsplit(".", 1)[-1] for d in EXPECTED_DEF_AXIOMS)
        da_body = ("import Probe.DefAx\nimport Probe.LusternaChecks\nopen Lusterna.Checks\n"
                   "set_option maxRecDepth 8000 in\n#eval show Lean.Meta.MetaM Unit from do\n"
                   f"  checkDefAxioms #[{da_pats}]\n  IO.println \"LUSTERNA_CHECK_DONE\"\n")
        assert not tools.write_out(deps, "lean/_defax_driver.lean", da_body).startswith("ERROR:")
        _, dout, derr = container.exec_in(cid, ["lake", "env", "lean", "_defax_driver.lean"],
                                          workdir=lean_dir, timeout=300)
        da_find, _, da_done = parse_findings(dout + "\n" + derr)
        got_footprint = {f["def"]: sorted(f.get("opaque", []))
                         for f in da_find if f.get("check") == "def_axioms"}
        footprint_ok = da_done is not None and got_footprint == EXPECTED_DEF_AXIOMS
        print(f"\ncheckDefAxioms {'OK' if footprint_ok else 'MISMATCH'}: {got_footprint}")

        # -- checkAnchorReference + checkAnchorCheckedReference: fidelity bridge + prong-3 split --
        an_body = ("import Probe.Schemas\nimport Probe.LusternaChecks\nopen Lusterna.Checks\n"
                   "set_option maxRecDepth 8000 in\n#eval show Lean.Meta.MetaM Unit from do\n"
                   "  checkAnchorReference #[`Probe.Schemas.s1_conforms, `Probe.Schemas.g_lemma_bridge] #[`f, `g, `nope]\n"
                   "  checkAnchorCheckedReference #[`Probe.Schemas.s1_conforms, `Probe.Schemas.g_lemma_bridge] #[`f, `g]\n"
                   "  IO.println \"LUSTERNA_CHECK_DONE\"\n")
        assert not tools.write_out(deps, "lean/_anchor_driver.lean", an_body).startswith("ERROR:")
        _, aout, aerr = container.exec_in(cid, ["lake", "env", "lean", "_anchor_driver.lean"],
                                          workdir=lean_dir, timeout=300)
        an_find, _, an_done = parse_findings(aout + "\n" + aerr)
        got_anchors = {f["anchor"]: bool(f.get("referenced"))
                       for f in an_find if f.get("check") == "anchor_bridge"}
        got_checked = {f["anchor"]: bool(f.get("checked_referenced"))
                       for f in an_find if f.get("check") == "anchor_checked"}
        anchor_ok = (an_done is not None
                     and got_anchors == {"f": True, "g": True, "nope": False}
                     and got_checked == {"f": True, "g": False})
        print(f"\nanchor {'OK' if anchor_ok else 'MISMATCH'}: bridge={got_anchors} checked={got_checked}")

        ok = (not (missing or spurious or rule_bad) and count_ok and footprint_ok and anchor_ok
              and not skipped)
        print("\nPASS" if ok else "\nFAIL")
        return 0 if ok else 1
    finally:
        sh("docker", "rm", "-f", CONTAINER_NAME, check=False)


def run_arbitrary(file: Path, targets: list[str], deps_files: list[Path],
                  subjects: list[str], conts: list[str]) -> int:
    sh("docker", "rm", "-f", CONTAINER_NAME, check=False)
    cid = sh("docker", "run", "--rm", "--detach", "--name", CONTAINER_NAME,
             "lusterna-toolchain:latest", "sleep", "infinity").strip()
    print(f"container {cid[:12]}")
    try:
        deps = AgentDeps(container_id=cid, repo_path=".", session_id="arbitrary", design_doc="")
        lean_dir = f"{container.OUT_IN}/lean"
        sh("docker", "exec", cid, "mkdir", "-p", lean_dir)
        # The root module (lib name) is whichever file's stem you pass as --file; dependency files
        # land alongside it. This mirrors the crate-root convention every other stage uses.
        lib_name = file.stem
        assert not tools.write_out(deps, f"lean/{file.name}", file.read_text()).startswith("ERROR:")
        for d in deps_files:
            assert not tools.write_out(deps, f"lean/{d.name}", d.read_text()).startswith("ERROR:")
        lean.setup_lake(deps)

        print(f"lake build (lib «{lib_name}») ...")
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            return sys.exit(f"file did not build:\n{out}\n{err}")

        imports = [f"import {lib_name}.LusternaChecks", f"import {file.stem}"]
        # The schema gate runs only when --subjects names the target function(s) under test.
        inv = [(t, subjects) for t in targets] if subjects else None
        findings, skipped, done, raw = run_checks(cid, lean_dir, imports, schema=inv)
        if done is None:
            print("\nDRIVER DID NOT COMPLETE — could not check (not evidence either way):")
            print(raw[-2000:])
            return 2
        print(f"\n{len(findings)} finding(s) over {len(targets)} target(s):\n")
        for f in findings:
            print(json.dumps(f, indent=2, ensure_ascii=False))
        for k in skipped:
            print("SKIPPED (not checked, not clean): " + json.dumps(k, ensure_ascii=False))
        if not findings:
            print("(none)")
        return 0
    finally:
        sh("docker", "rm", "-f", CONTAINER_NAME, check=False)


GATE_CONTAINER = "lusterna-checklean-gate"


def run_gate_fixture() -> int:
    """Drive the REAL taint gate — `lean.target_footprint_gate` (build + `checkDefAxioms` + the
    block/pass decision + its categorised feedback + fail-closed handling) — over the Leaky
    fixture: a realistically shaped translation with a CLEAN target and a target that transitively reaches a
    `Display`/`fmt` opaque. The DefAx fixture covers the Lean checker; THIS covers the Python gate
    WRAPPER (the trusted verdict the TRANSLATE stage blocks on). Asserts BLOCK / PASS / fail-closed.
    Also validates `lean.impl_references` (the impl-verified partition) on the open'd-short-name case
    that a text scan got wrong."""
    sh("docker", "rm", "-f", GATE_CONTAINER, check=False)
    cid = sh("docker", "run", "--rm", "--detach", "--name", GATE_CONTAINER,
             "lusterna-toolchain:latest", "sleep", "infinity").strip()
    print(f"\n[gate fixture] container {cid[:12]}")
    try:
        deps = AgentDeps(container_id=cid, repo_path=HERE, session_id="gate",
                         design_doc="", campaign="Gate")
        lean_dir = f"{container.OUT_IN}/lean"
        sh("docker", "exec", cid, "mkdir", "-p", lean_dir)
        translation = (HERE / "Leaky.lean").read_text()
        assert not tools.write_out(deps, "lean/LeakyModel.lean", translation).startswith("ERROR:")
        lean.setup_lake(deps)
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            print(f"gate fixture did not build:\n{out}\n{err}")
            return 1
        files, lp = ["lean/LeakyModel.lean"], "lean/LeakyModel.lean"
        deps.progress["aeneas"] = {"lean_path": lp, "lean_files": files, "holes": []}

        def gate(patterns: list[str]) -> dict:
            deps.progress["target_patterns"] = patterns
            return lean.target_footprint_gate(deps, translation, files, lp)

        # leaky+clean → BLOCK: only `leakyOp` flagged, feedback categorises the Display leak.
        g1 = gate(["crate::_::cleanTarget", "crate::_::leakyOp"])
        case1 = (not g1["ok"] and any("leakyOp" in d for d in g1["footprint"])
                 and all("cleanTarget" not in d for d in g1["footprint"])
                 and "Display" in g1["feedback"])
        # clean-only → PASS (empty footprint).
        g2 = gate(["crate::_::cleanTarget"])
        case2 = g2["ok"] and not g2["footprint"]
        # nonexistent target → BLOCK, fail-closed (the vacuous false-clean a name mismatch produces).
        g3 = gate(["crate::_::does_not_exist"])
        case3 = (not g3["ok"]) and "matched NO target" in g3["feedback"]

        ok = True
        for name, passed in [("leaky → BLOCK", case1), ("clean → PASS", case2),
                             ("vacuous → fail-closed BLOCK", case3)]:
            print(f"  taint gate: {name}: {'PASS' if passed else 'FAIL'}")
            ok = ok and passed
        if not case1:
            print(f"    (leaky footprint={g1['footprint']}, ok={g1['ok']})")

        # ── impl_references: does a theorem VERIFY THE IMPLEMENTATION? ─────────────────────────────
        # The exact bug this replaced: `ts_uses_impl` references the translated `cleanTarget` via the
        # OPENED short name (`open leaky`), which a text scan of the full in-namespace def name
        # missed → every theorem wrongly "abstract". `pure_lemma` is genuinely abstract (Nat only).
        spec = ("import LeakyModel\nopen leaky\n"
                "namespace LeakyModel.Spec.Property\n"
                "theorem ts_uses_impl (x : Nat) (h : cleanTarget x = 1) : True := trivial\n"
                "theorem pure_lemma (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
                "end LeakyModel.Spec.Property\n")
        assert not tools.write_out(deps, "lean/LeakyModel/Spec/Solvency.lean", spec).startswith("ERROR:")
        code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
        if code != 0:
            print(f"  impl_references: spec did not build:\n{out}\n{err}")
            ok = False
        else:
            refs = lean.impl_references(deps, "lean/LeakyModel/Spec/Solvency.lean")
            case4 = refs.get("ts_uses_impl") is True and refs.get("pure_lemma") is False
            print(f"  impl_references (open'd name → impl-verified, abstract → not): "
                  f"{'PASS' if case4 else 'FAIL'}")
            if not case4:
                print(f"    (got {refs})")
            ok = ok and case4
        return 0 if ok else 1
    finally:
        sh("docker", "rm", "-f", GATE_CONTAINER, check=False)


DRIFT_CONTAINER = "lusterna-checklean-drift"


def run_drift_fixture() -> int:
    """Drive the statement-drift detector — `lean.dump_statement_types` (the Lean `dumpStatementTypes`
    check) + `lean.check_statement_drift` — against the REAL toolchain. Builds a two-theorem spec,
    fingerprints it (the FORMALISE baseline), then rebuilds with one statement CHANGED and one later
    REMOVED, asserting the Lean type-hashes are stable-yet-discriminating and the drift is classified
    as drifted / accounted-by-refutation / removed. This is the behavioural counterpart to the Python
    unit tests, which only diff canned hashes."""
    sh("docker", "rm", "-f", DRIFT_CONTAINER, check=False)
    cid = sh("docker", "run", "--rm", "--detach", "--name", DRIFT_CONTAINER,
             "lusterna-toolchain:latest", "sleep", "infinity").strip()
    print(f"\n[drift fixture] container {cid[:12]}")
    try:
        deps = AgentDeps(container_id=cid, repo_path=HERE, session_id="drift",
                         design_doc="", campaign="Drift")
        lean_dir = f"{container.OUT_IN}/lean"
        sh("docker", "exec", cid, "mkdir", "-p", f"{lean_dir}/DriftCrate/Spec")
        assert not tools.write_out(deps, "lean/DriftCrate.lean", "-- crate root\n").startswith("ERROR:")
        deps.progress["aeneas"] = {"lean_path": "lean/DriftCrate.lean", "lean_files": [], "holes": []}
        spec_rel = "lean/DriftCrate/Spec/Drift.lean"
        module = lean.campaign_spec_module(deps)          # DriftCrate.Spec.Drift

        def build_spec(src: str) -> bool:
            assert not tools.write_out(deps, spec_rel, src).startswith("ERROR:")
            code, out, err = container.exec_in(cid, ["lake", "build"], workdir=lean_dir, timeout=1800)
            if code != 0:
                print(f"  drift fixture did not build:\n{out}\n{err}")
            return code == 0

        lean.setup_lake(deps)
        # v1 — the FORMALISE baseline: two theorems.
        if not build_spec("theorem tA : 1 + 1 = 2 := by rfl\ntheorem tB : 2 + 2 = 4 := by rfl\n"):
            return 1

        base = lean.dump_statement_types(deps, module)
        again = lean.dump_statement_types(deps, module)
        ok = True

        def check(name: str, passed: bool, detail: str = "") -> None:
            nonlocal ok
            print(f"  {name}: {'PASS' if passed else 'FAIL'}{('  ' + detail) if not passed and detail else ''}")
            ok = ok and passed

        check("enumerate: both theorems fingerprinted", set(base) == {"tA", "tB"}, str(base))
        check("hashes non-empty", all(base.values()), str(base))
        check("deterministic across a rebuild-free re-dump", again == base, f"{again} vs {base}")
        deps.progress["formalise_types"] = base

        # v2 — tB's STATEMENT changed (still provable), tA untouched → tB unaccounted, tA stable.
        if not build_spec("theorem tA : 1 + 1 = 2 := by rfl\ntheorem tB : 0 = 0 := by rfl\n"):
            return 1
        d = lean.check_statement_drift(deps, refuted=[])
        check("changed statement → unaccounted, other stable",
              d["unaccounted"] == ["tB"] and d["stable"] == ["tA"] and not d["accounted"], str(d))
        # same change, but with a refutation witness for tB → accounted, not flagged.
        d = lean.check_statement_drift(deps, refuted=["tB"])
        check("changed statement WITH refutation → accounted",
              d["accounted"] == ["tB"] and not d["unaccounted"], str(d))

        # v3 — tB removed entirely → unaccounted (no witness; removed collapses into unaccounted).
        if not build_spec("theorem tA : 1 + 1 = 2 := by rfl\n"):
            return 1
        d = lean.check_statement_drift(deps, refuted=[])
        check("removed statement → unaccounted, other stable",
              d["unaccounted"] == ["tB"] and d["stable"] == ["tA"] and not d["accounted"], str(d))
        return 0 if ok else 1
    finally:
        sh("docker", "rm", "-f", DRIFT_CONTAINER, check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, help="an arbitrary .lean file to check (root module)")
    ap.add_argument("--targets", help="comma-separated fully-qualified theorem/def names to check")
    ap.add_argument("--deps", nargs="*", type=Path, default=[], help="extra .lean files the target imports")
    ap.add_argument("--subjects", help="comma-separated function names under test; enables the target-aware checks")
    ap.add_argument("--continuations", default="",
                    help="comma-separated functions a theorem may legitimately chain through")
    args = ap.parse_args()

    if not shutil_which("docker"):
        sys.exit("docker not on PATH")
    if args.file:
        if not args.targets:
            sys.exit("--targets is required with --file")
        return run_arbitrary(args.file, [t.strip() for t in args.targets.split(",")], args.deps,
                             [g.strip() for g in (args.subjects or "").split(",") if g.strip()],
                             [g.strip() for g in args.continuations.split(",") if g.strip()])
    # Two independent fixtures (separate containers — each wires its own lake lib): the spec-gate /
    # checker regression, and the taint-gate wrapper. Both must pass.
    rc = run_fixture()
    rc = run_gate_fixture() or rc
    rc = run_drift_fixture() or rc
    return rc


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


if __name__ == "__main__":
    raise SystemExit(main())
