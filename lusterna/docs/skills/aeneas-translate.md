# Aeneas translatability playbook (TRANSLATE stage)

This is a decision playbook for turning a Rust target into a clean Lean translation with Charon +
Aeneas. It tells you, per construct, whether Aeneas will translate it, opaque it, hole it, or reject
it — and the behaviour-preserving recipe when you must fix it. **It is measured, not folklore:** the
verdict table below is produced by `tools/aeneas-characterize` running one probe per construct through
this exact toolchain, and the "modelable stdlib" set is extracted from Aeneas's own builtin registry
(`extract/ExtractBuiltin*.ml`). It is **version-locked to the toolchain image** — regenerate it on a
toolchain bump.

## The one rule

**No mocks/stubs unless they are irrelevant to the target.** An `--opaque` dependency (or a `sorry`
hole) is a mock: it becomes a bare Lean `axiom`/`sorry` with no relating equations. It is acceptable
ONLY when its behaviour is irrelevant to every property you will verify — its result/effect never
flows into anything a property constrains (formatting/logging, or a value the properties treat as an
abstract token: a curve point, hash, transcript, RNG draw). If a target function reads/writes it and a
property depends on the outcome, you MUST **model** it (rung 3), not opaque it — otherwise the
translation compiles but the properties are unprovable, and TRANSLATE has achieved nothing.

## Verdict table (measured on this toolchain)

`def` = translated (use freely) · `axiom` = opaque, no Lean model (model it if a property depends on it)
· `hole` = body left `sorry` (same treatment as axiom) · `error` = rejected / aborts (must refactor away).

| Construct | Verdict | If a property depends on it |
| --- | --- | --- |
| `Vec` index / `push` / `new` / deref, slice indexing | **def** | use as-is |
| `Option::unwrap_or`, `Option::unwrap` | **def** | use as-is |
| generic closures (`FnMut`/`FnOnce`, monomorphized) | **def** | use as-is |
| single loop without early-return | **def** | use as-is |
| `BTreeMap` / `HashMap` get/insert/entry | **axiom** | MODEL → assoc-list |
| `Option::ok_or` / `map` / `and_then` / `copied` (combinators w/o a builtin) | **axiom** | MODEL → explicit `match` |
| iterator-adaptor chains (`.iter().map().sum()` …) | **axiom** | MODEL → explicit loop |
| `f32`/`f64` arithmetic | **hole** | out of scope (no float model) — opaque only if irrelevant |
| byte-string literals `b"…"`, raw-pointer deref/aggregate | **hole** | refactor away or opaque if irrelevant |
| `&dyn Trait` dispatch | **hole** | MODEL → monomorphize to a concrete type / generic param |
| `transmute` | **axiom** | refactor away (unsafe reinterpretation is not modelable) |
| function pointers, arrow-typed `static` (e.g. `fn(...) -> ...`) | **error** | monomorphize (generic param / direct call) |
| `union` | **error** | replace with an enum/struct if behaviour permits |
| Generic Associated Types (GAT) | **error** | monomorphize the associated type |
| labeled `break`/`continue` to an outer loop | **error** | restructure to a flag + single exit |
| `return` inside a (nested) loop | **error** | restructure to a mutable result + single exit |

(Constructs Aeneas rejects that are unreachable from safe Rust — `mut`-recursive globals, unwinding,
raw pointer metadata — are not listed; you won't hit them from a normal verification target.)

## Modelable stdlib (the opaque-vs-def line)

The verdict "def vs axiom" for a stdlib item is decided by Aeneas's builtin registry: **in the registry
→ has a Lean model → `def`; absent → emitted as an `axiom`.** Present (so translate as-is): `alloc::vec::Vec`
(new/index/deref/push), `core::option::Option` core, `core::slice::index::*`. **Absent (so opaqued):**
`alloc::collections::btree::*` (BTreeMap), `HashMap`, and most `Option`/`Result`/iterator *combinators*.
So a map/collection the properties are about is always the case that needs modeling.

## Recipe library (behaviour-preserving)

Model in the **Rust source** (`/workspace/repo/src/*.rs`), confirm behaviour with `cargo test`, then
re-translate. You cannot add Aeneas builtins — the source is the only lever.

- **Map the properties read/write (`BTreeMap`/`HashMap`) → association list `Vec<(K, V)>`.**
  `get(k)` → linear scan returning the matching value; `insert(k, v)` → update-in-place if `k`
  present else `push((k, v))` (last-write-wins). Preserves the key→value mapping. (Only unsafe if the
  program observes iteration *order*; token ledgers don't.)
- **`Option`/`Result` combinator → explicit `match`.** `o.ok_or(e)` → `match o { Some(v) => Ok(v),
  None => Err(e) }`; `o.map(f)` → `match o { Some(v) => Some(f(v)), None => None }`; `?` → `match … {
  Ok(v) => v, Err(e) => return Err(e.into()) }` (mind the loop-return rule below).
- **Iterator-adaptor chain → explicit `for`/`while` loop** accumulating into a mutable local.
  `v.iter().map(|x| g(x)).sum()` → `let mut acc = 0; for x in v { acc += g(x); } acc`.
- **`&dyn Trait` / trait object → monomorphize.** Replace with a concrete type, or make the caller
  generic (`fn f<T: Trait>(x: &T)`) — generic + monomorphized dispatch IS translatable.
- **fn pointer / arrow-typed global → monomorphize or inline.** Replace the indirection with a
  generic type parameter or a direct call.
- **Early `return` / labeled `break`/`continue` inside loops → single-exit form.** Introduce a mutable
  result and a boolean/`while cond && !done` guard; set the result and let the loop fall through to one
  exit. No early `return`, no labeled jumps.

## Charon / Aeneas mechanics

- **Name matcher — the usual trap:** `--start-from` resolves in the crate being built (the `-p` one),
  so it uses the `crate::` keyword (`crate::module::_::method` for a method). `--opaque`/`--exclude`
  match fully-qualified names, so they use the REAL crate name (`solana_zk_sdk::…`, `core::fmt::…`).
- **One clean invocation (avoids the polluted-tree rejection):** clear the dest and run once with
  `-split-files`, which yields the required nested layout (`lean/<Crate>.lean` + `lean/<Crate>/`):
  `rm -rf /workspace/out/lean/* && aeneas -backend lean -split-files -dest /workspace/out/lean <crate>.llbc`.
  A second run in a different layout leaves orphan top-level modules and is rejected.
- **Trusted primitives to opaque/exclude** (irrelevant to properties): `--exclude core::fmt::Debug::*`,
  `--opaque core::fmt::Formatter`, and crypto/curve/hash/transcript/RNG leaves.

## Workflow

1. Scope with `--start-from` to the target; run charon → aeneas (one clean `-split-files` run).
2. Read the result: which target defs exist, which items became `axiom`s or `sorry` holes.
3. For each opaqued/holed item the target's own logic touches: apply the ONE rule — irrelevant → leave
   opaque (an assumption); property-relevant → apply the matching recipe in the source.
4. `cargo test` to confirm behaviour is preserved; re-translate; repeat until the target functions are
   real `def`s and it compiles.

## Provenance

Verdicts and the registry are regenerated by `tools/aeneas-characterize/characterize.py`
(`characterization.json`), which measures this toolchain directly. Coverage of Aeneas's rejection-site
message set is partial by design — many sites are narrow/legacy paths a real target never reaches — but
the per-construct verdicts above are measured, not assumed. Re-run it when the toolchain image changes.
