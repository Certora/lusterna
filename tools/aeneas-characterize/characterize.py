#!/usr/bin/env python3
"""Aeneas translatability characterization harness (build-time / dev tool, NOT pipeline runtime).

Runs a corpus of tiny single-construct probe crates through charon+aeneas ONCE in the toolchain
container and records, per construct, the precise verdict — def (translatable) / hole (`sorry`) /
axiom (opaque, no Lean model) / error (rejected) — plus the Aeneas source message it triggered.

It then measures coverage against S.json (the ~40 source-defined fragment-boundary messages):
    coverage = |triggered ∩ S| / |S_probeable|,  and prints the S\\T gap (what we haven't exercised).
Also extracts the builtin registry (extract/ExtractBuiltin*.ml) — the authoritative "what stdlib has
a Lean model" set that governs the opaque-axiom verdict.

Output = a JSON report (stdout / --out) that seeds docs/skills/aeneas-translate.md. Version-locked to
the image's Aeneas; re-run on a toolchain bump. Usage:  python tools/aeneas-characterize/characterize.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

# Reuse the pipeline's container plumbing (editable-installed lusterna package).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lusterna import container as C  # noqa: E402

IMAGE = "lusterna-toolchain:latest"
AENEAS_SRC = "/opt/aeneas/src"
HERE = Path(__file__).resolve().parent

# ── probe corpus: one construct each. `expect` is what we predict; the run records the truth. ──
# Kept dep-free so `charon cargo` builds in seconds. edition 2021.
PROBES = [
    ("vec_index",        "def",   "pub fn probe(v: &Vec<u64>, i: usize) -> u64 { v[i] }"),
    ("vec_push",         "def",   "pub fn probe(mut v: Vec<u64>) -> Vec<u64> { v.push(1); v }"),
    ("btreemap_get",     "axiom", "use std::collections::BTreeMap;\n"
                                  "pub fn probe(m: &BTreeMap<u64,u64>, k: u64) -> u64 { *m.get(&k).unwrap_or(&0) }"),
    ("hashmap_get",      "axiom", "use std::collections::HashMap;\n"
                                  "pub fn probe(m: &HashMap<u64,u64>, k: u64) -> u64 { *m.get(&k).unwrap_or(&0) }"),
    ("option_ok_or",     "axiom", "pub fn probe(o: Option<u64>) -> Result<u64,()> { o.ok_or(()) }"),
    ("option_map",       "axiom", "pub fn probe(o: Option<u64>) -> Option<u64> { o.map(|x| x + 1) }"),
    ("option_unwrap_or", "def",   "pub fn probe(o: Option<u64>) -> u64 { o.unwrap_or(0) }"),
    ("iterator_chain",   "axiom", "pub fn probe(v: &[u64]) -> u64 { v.iter().map(|x| x + 1).sum() }"),
    ("closure_fnmut",    "def",   "pub fn probe<F: FnMut(u64) -> u64>(mut f: F, x: u64) -> u64 { f(x) }"),
    ("fn_pointer_param", "error", "pub fn probe(f: fn(u64) -> u64, x: u64) -> u64 { f(x) }"),
    ("arrow_global",     "error", "fn id(x: u64) -> u64 { x }\npub static F: fn(u64) -> u64 = id;\n"
                                  "pub fn probe() -> fn(u64) -> u64 { F }"),
    ("float",            "error", "pub fn probe(x: f64) -> f64 { x + 1.0 }"),
    ("union",            "error", "pub union U { pub a: u64, pub b: u64 }\n"
                                  "pub fn probe(u: &U) -> u64 { unsafe { u.a } }"),
    ("dyn_trait",        "error", "pub trait Speak { fn v(&self) -> u64; }\n"
                                  "pub fn probe(x: &dyn Speak) -> u64 { x.v() }"),
    ("gat",              "error", "pub trait Container { type Item<'a> where Self: 'a; \n"
                                  "  fn first<'a>(&'a self) -> Self::Item<'a>; }"),
    ("nested_borrows",   "error", "pub fn probe(x: &mut &mut u64) { **x += 1; }"),
    ("byte_string_lit",  "error", "pub fn probe() -> &'static [u8] { b\"hi\" }"),
    ("raw_ptr_deref",    "error", "pub unsafe fn probe(p: *const u64) -> u64 { *p }"),
    ("raw_ptr_aggregate","error", "pub fn probe(s: &[u64]) -> *const [u64] { s as *const [u64] }"),
    ("transmute",        "error", "pub unsafe fn probe(x: u64) -> i64 { core::mem::transmute(x) }"),
    ("early_return_loop","error", "pub fn probe(n: u64) -> u64 {\n"
                                  "  let mut i = 0; while i < n { if i > 3 { return i; } i += 1; } 0 }"),
    ("break_outer_loop", "error", "pub fn probe(n: u64) -> u64 {\n"
                                  "  let mut c = 0; 'outer: while c < n { let mut j = 0; while j < n {\n"
                                  "    if j > 2 { break 'outer; } j += 1; } c += 1; } c }"),
    ("continue_outer",   "error", "pub fn probe(n: u64) -> u64 {\n"
                                  "  let mut c = 0; 'outer: while c < n { c += 1; let mut j = 0; while j < n {\n"
                                  "    if j > 2 { continue 'outer; } j += 1; } } c }"),
    ("nested_loop_ret",  "error", "pub fn probe(n: u64) -> u64 {\n"
                                  "  let mut i = 0; while i < n { let mut j = 0; while j < n {\n"
                                  "    if i * j > 10 { return i; } j += 1; } i += 1; } 0 }"),
]

CARGO_TOML = ('[package]\nname = "probe_{id}"\nversion = "0.0.0"\nedition = "2021"\n'
              '[lib]\npath = "src/lib.rs"\n')


def put(cid: str, path: str, content: str) -> None:
    parent = str(Path(path).parent)
    subprocess.run(["docker", "exec", "-i", cid, "sh", "-c", f"mkdir -p {parent} && cat > {path}"],
                   input=content, text=True, check=True)


def match_S(text: str, S: list[dict]) -> list[str]:
    return sorted({e["id"] for e in S if e["msg"] in text})


def run_probe(cid: str, pid: str, expect: str, rust: str, S: list[dict]) -> dict:
    root = f"/probe/{pid}"
    C.exec_in(cid, ["rm", "-rf", root])
    put(cid, f"{root}/Cargo.toml", CARGO_TOML.format(id=pid))
    put(cid, f"{root}/src/lib.rs", rust)
    ch_code, _, ch_err = C.exec_in(cid, [C_bin("charon"), "cargo", "--preset=aeneas",
                                         "--", "-p", f"probe_{pid}"], workdir=root, timeout=300)
    llbc = f"{root}/probe_{pid}.llbc"
    msgs = ch_err
    outcome, ae_err = None, ""
    if ch_code != 0 or C.exec_in(cid, ["test", "-f", llbc])[0] != 0:
        outcome = "charon-error"
    else:
        C.exec_in(cid, ["rm", "-rf", f"{root}/lean"])
        ae_code, ae_out, ae_err = C.exec_in(cid, [C_bin("aeneas"), "-backend", "lean",
                                                  "-dest", f"{root}/lean", llbc], workdir=root, timeout=180)
        msgs += "\n" + ae_err + "\n" + ae_out
        _, lean, _ = C.exec_in(cid, ["sh", "-c", f"cat {root}/lean/*.lean 2>/dev/null"])
        if not lean.strip():
            outcome = "aeneas-error"
        else:
            probe_def = re.search(r"(?m)^def\s+[\w.]*probe\b(.*?)(?=^def |\Z)", lean, re.S)
            block = probe_def.group(0) if probe_def else ""
            if not probe_def:
                outcome = "no-def"
            elif re.search(r"(?m)^\s*sorry\s*$", block):
                outcome = "hole"
            elif re.search(r"(?m)^axiom\s", lean):
                outcome = "axiom"
            else:
                outcome = "def"
    return {"id": pid, "expect": expect, "outcome": outcome,
            "matched_S": match_S(msgs, S),
            "msg_sample": _errsample(ch_err + "\n" + ae_err)}


def _errsample(text: str) -> str:
    lines = [l.strip() for l in text.splitlines()
             if any(k in l for k in ("[Error]", "error:", "Error:", "not supported", "nsupported",
                                     "not handled", "not implemented", "Unimplemented"))]
    return " | ".join(dict.fromkeys(lines))[:400]


def C_bin(name: str) -> str:
    return name  # charon/aeneas are on PATH in the image


def extract_registry(cid: str) -> dict:
    """The builtin registry = rust paths registered with a Lean model in extract/ExtractBuiltin*.ml.
    A stdlib item present here translates; absent → emitted as an axiom (the opaque verdict)."""
    _, out, _ = C.exec_in(cid, ["sh", "-c",
        f"grep -rhoE '\"[a-zA-Z_][a-zA-Z0-9_]*(::[a-zA-Z0-9_<>]+)+\"' "
        f"{AENEAS_SRC}/extract/ExtractBuiltin.ml {AENEAS_SRC}/extract/ExtractBuiltinLean.ml 2>/dev/null"])
    names = sorted({l.strip().strip('"') for l in out.splitlines() if "::" in l})
    return {"count": len(names), "names": names}


def main() -> None:
    S = json.loads((HERE / "S.json").read_text())["entries"]
    probeable = [e for e in S if e.get("probeable")]
    cid = C.start(image=IMAGE)
    try:
        results = [run_probe(cid, pid, expect, rust, S) for pid, expect, rust in PROBES]
        registry = extract_registry(cid)
    finally:
        C.stop(cid)

    triggered = sorted({sid for r in results for sid in r["matched_S"]})
    probeable_ids = {e["id"] for e in probeable}
    hit = sorted(set(triggered) & probeable_ids)
    gap = sorted(probeable_ids - set(triggered))
    report = {
        "S_total": len(S), "S_probeable": len(probeable),
        "triggered_ids": triggered,
        "coverage_probeable": f"{len(hit)}/{len(probeable)}",
        "gap_probeable_not_triggered": gap,
        "probes": results,
        "registry": registry,
    }
    out = HERE / "characterization.json"
    out.write_text(json.dumps(report, indent=2))

    print(f"\n=== Aeneas characterization ===")
    print(f"probes: {len(results)}   S (total/probeable): {len(S)}/{len(probeable)}")
    print(f"coverage (probeable S triggered): {len(hit)}/{len(probeable)}")
    print(f"registry (modelable stdlib): {registry['count']} names\n")
    print(f"{'probe':<18}{'expect':<8}{'outcome':<14}matched_S")
    for r in results:
        print(f"{r['id']:<18}{r['expect']:<8}{r['outcome'] or '?':<14}{','.join(r['matched_S']) or '-'}")
    print(f"\nS\\T gap (probeable, not yet triggered): {gap or '(none)'}")
    print(f"\nfull report → {out}")


if __name__ == "__main__":
    main()
