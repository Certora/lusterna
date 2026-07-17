#!/usr/bin/env python3
"""Aeneas translatability inventory (build-time / dev tool, NOT pipeline runtime).

Runs a corpus of tiny single-construct probe crates through charon+aeneas ONCE in the toolchain
container and records, per construct, the measured verdict:

    def   — translated cleanly (use as-is)
    axiom — opaqued, no Lean model (must be modelled if a property depends on it)
    hole  — body left as `sorry`
    error — rejected by charon/aeneas (must be refactored away)

plus any Aeneas/charon "unsupported …" message it emitted. It also extracts the builtin registry
(extract/ExtractBuiltin*.ml) — the authoritative "what stdlib has a Lean model" set that decides
def-vs-axiom. The output (characterization.json) is the measured basis for the TRANSLATE playbook
(docs/skills/aeneas-translate.md); it is version-locked to the toolchain image — re-run on a bump.

This is an INVENTORY, not a coverage metric: we enumerate the constructs the playbook needs to speak
to and record what the toolchain actually does with each. Usage:
    python tools/aeneas-characterize/characterize.py
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

# ── probe corpus: one construct each. `expect` is the prediction; the run records the truth. ──
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
    ("float",            "hole",  "pub fn probe(x: f64) -> f64 { x + 1.0 }"),
    ("union",            "error", "pub union U { pub a: u64, pub b: u64 }\n"
                                  "pub fn probe(u: &U) -> u64 { unsafe { u.a } }"),
    ("dyn_trait",        "hole",  "pub trait Speak { fn v(&self) -> u64; }\n"
                                  "pub fn probe(x: &dyn Speak) -> u64 { x.v() }"),
    ("gat",              "error", "pub trait Container { type Item<'a> where Self: 'a; \n"
                                  "  fn first<'a>(&'a self) -> Self::Item<'a>; }"),
    ("nested_borrows",   "def",   "pub fn probe(x: &mut &mut u64) { **x += 1; }"),
    ("byte_string_lit",  "hole",  "pub fn probe() -> &'static [u8] { b\"hi\" }"),
    ("raw_ptr_deref",    "hole",  "pub unsafe fn probe(p: *const u64) -> u64 { *p }"),
    ("transmute",        "axiom", "pub unsafe fn probe(x: u64) -> i64 { core::mem::transmute(x) }"),
    ("early_return_loop","error", "pub fn probe(n: u64) -> u64 {\n"
                                  "  let mut i = 0; while i < n { if i > 3 { return i; } i += 1; } 0 }"),
    ("nested_loop_ret",  "error", "pub fn probe(n: u64) -> u64 {\n"
                                  "  let mut i = 0; while i < n { let mut j = 0; while j < n {\n"
                                  "    if i * j > 10 { return i; } j += 1; } i += 1; } 0 }"),
]

CARGO_TOML = ('[package]\nname = "probe_{id}"\nversion = "0.0.0"\nedition = "2021"\n'
              '[lib]\npath = "src/lib.rs"\n')

# words that mark an "unsupported construct" message (vs an internal invariant)
_UNSUP = ("support", "unimpl", "not handled", "not implemented", " yet", "cannot", "no builtin")


def put(cid: str, path: str, content: str) -> None:
    parent = str(Path(path).parent)
    subprocess.run(["docker", "exec", "-i", cid, "sh", "-c", f"mkdir -p {parent} && cat > {path}"],
                   input=content, text=True, check=True)


def _message(text: str) -> str:
    """The distinctive 'unsupported …' line(s) charon/aeneas emitted, if any."""
    lines = [l.strip() for l in text.splitlines()
             if any(k in l.lower() for k in _UNSUP) and ("error" in l.lower() or "[" in l or "support" in l.lower())]
    return " | ".join(dict.fromkeys(lines))[:300]


def run_probe(cid: str, pid: str, expect: str, rust: str) -> dict:
    root = f"/probe/{pid}"
    C.exec_in(cid, ["rm", "-rf", root])
    put(cid, f"{root}/Cargo.toml", CARGO_TOML.format(id=pid))
    put(cid, f"{root}/src/lib.rs", rust)
    ch_code, _, ch_err = C.exec_in(cid, ["charon", "cargo", "--preset=aeneas",
                                         "--", "-p", f"probe_{pid}"], workdir=root, timeout=300)
    llbc = f"{root}/probe_{pid}.llbc"
    msgs, ae_err = ch_err, ""
    if ch_code != 0 or C.exec_in(cid, ["test", "-f", llbc])[0] != 0:
        verdict = "error"
    else:
        C.exec_in(cid, ["rm", "-rf", f"{root}/lean"])
        _, ae_out, ae_err = C.exec_in(cid, ["aeneas", "-backend", "lean", "-dest",
                                            f"{root}/lean", llbc], workdir=root, timeout=180)
        msgs += "\n" + ae_err + "\n" + ae_out
        _, lean, _ = C.exec_in(cid, ["sh", "-c", f"cat {root}/lean/*.lean 2>/dev/null"])
        if not lean.strip():
            verdict = "error"
        else:
            block = re.search(r"(?m)^def\s+[\w.]*probe\b(.*?)(?=^def |\Z)", lean, re.S)
            if not block:
                verdict = "error"          # produced Lean but not a `def probe` (e.g. rejected item)
            elif re.search(r"(?m)^\s*sorry\s*$", block.group(0)):
                verdict = "hole"
            elif re.search(r"(?m)^axiom\s", lean):
                verdict = "axiom"
            else:
                verdict = "def"
    return {"id": pid, "expect": expect, "verdict": verdict,
            "message": _message(ch_err + "\n" + ae_err)}


def extract_registry(cid: str) -> dict:
    """The builtin registry = rust paths registered with a Lean model in extract/ExtractBuiltin*.ml.
    A stdlib item present here translates; absent → emitted as an axiom (the opaque verdict)."""
    _, out, _ = C.exec_in(cid, ["sh", "-c",
        f"grep -rhoE '\"[a-zA-Z_][a-zA-Z0-9_]*(::[a-zA-Z0-9_<>]+)+\"' "
        f"{AENEAS_SRC}/extract/ExtractBuiltin.ml {AENEAS_SRC}/extract/ExtractBuiltinLean.ml 2>/dev/null"])
    names = sorted({l.strip().strip('"') for l in out.splitlines() if "::" in l})
    return {"count": len(names), "names": names}


def main() -> None:
    cid = C.start(image=IMAGE)
    try:
        probes = [run_probe(cid, pid, expect, rust) for pid, expect, rust in PROBES]
        registry = extract_registry(cid)
    finally:
        C.stop(cid)

    report = {"probes": probes, "registry": registry}
    out = HERE / "characterization.json"
    out.write_text(json.dumps(report, indent=2))

    print("\n=== Aeneas translatability inventory ===")
    print(f"probes: {len(probes)}   registry (modelable stdlib): {registry['count']} names\n")
    print(f"{'construct':<18}{'expect':<8}{'verdict':<8}message")
    for p in probes:
        flag = "" if p["verdict"] == p["expect"] else "  ← differs"
        print(f"{p['id']:<18}{p['expect']:<8}{p['verdict']:<8}{p['message'] or '-'}{flag}")
    print(f"\nfull inventory → {out}")


if __name__ == "__main__":
    main()
