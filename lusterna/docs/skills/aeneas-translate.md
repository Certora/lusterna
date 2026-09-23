# Aeneas translatability playbook (TRANSLATE stage)

This is a decision playbook for turning a Rust target into a clean Lean translation with Charon +
Aeneas. It does NOT carry a fixed list of what translates: Aeneas's fragment shifts with the nightly
toolchain, so any static verdict table goes stale. Instead it tells you how to **identify** what the
toolchain opaqued, holed, or rejected on THIS image, and the behaviour-preserving **recipe** to fix
each case. The toolchain in the container is the source of truth — read it, don't memorise it.

## The one rule

**No mocks/stubs unless they are irrelevant to the target.** An `--opaque` dependency (or a `sorry`
hole) is a mock: it becomes a bare Lean `axiom`/`sorry` with no relating equations. It is acceptable
ONLY when its behaviour is irrelevant to every property you will verify — its result/effect never
flows into anything a property constrains (formatting/logging, or a value the properties treat as an
abstract token: a curve point, hash, transcript, RNG draw). If a target function reads/writes it and a
property depends on the outcome, you MUST **model** it (rung 3), not opaque it — otherwise the
translation compiles but the properties are unprovable, and TRANSLATE has achieved nothing.

⚠ **"Irrelevant" is TRANSITIVE, and you cannot eyeball it.** `#print axioms` (the downstream gate)
takes the axiom closure over a proof term, which runs through a function's ENTIRE body — every error,
panic, and formatting branch, not just the value path. So an opaque axiom a target reaches through
*any* path — a `Display`/`Debug` call on an error branch, a `?`-propagated parse, a serialization
helper — taints EVERY theorem about that function, even one that never exercises that branch. Do NOT
reason your way to "this path is inert, no value flows through it": that reasoning is exactly what a
transitive closure ignores. A leaf is irrelevant only if NO target's body can reach it at all.

## Identify what Aeneas could not translate

Two complementary signals, both read from the LIVE toolchain — never a remembered list.

**1. Read what the toolchain produced (primary — always current).** After translating, open the
generated Lean under `/workspace/out/lean` and inspect each target and the constants it reaches:

- `def …` — translated cleanly; use as-is.
- `axiom …` — OPAQUED: Aeneas has no Lean model for it, so it is an assumption with no defining
  equations. If any property depends on its behaviour you must MODEL it (recipe below); otherwise it
  is acceptable only under the one rule above.
- a body that is `sorry` — HOLE: same treatment as an axiom.
- a target with NO `def` (and no `.llbc` produced), with `not supported` / `unsupported` printed by
  charon/aeneas on stderr — REJECTED outright: the construct must be refactored away (recipe below).

Then run `#print axioms` on a theorem: that is the real verdict. It closes over the WHOLE proof term
(every error/panic/format branch, per the one rule), so it surfaces an opaque leaf reached through ANY
path — including ones you would never spot by reading the value path.

**2. Look it up in Aeneas's source (to predict or confirm a stdlib item).** Whether a stdlib item
becomes a `def` or an `axiom` is decided by Aeneas's builtin registry: **in the registry → it has a
Lean model → `def`; absent → emitted as an `axiom`.** The registry is source you can read directly in
the container:

- `/opt/aeneas/src/extract/ExtractBuiltin.ml` — the backend-agnostic registry (`mk_type`, `mk_trait`,
  the fun/globals maps); Rust paths appear as string literals (`"alloc::vec::Vec"`,
  `"core::option::Option"`, `"core::ops::index::Index"`).
- `/opt/aeneas/src/extract/ExtractBuiltinLean.ml` — the Lean model NAME each registered path maps to.
- `/opt/aeneas/src/extract/ExtractBuiltinCore.ml` — shared core registrations.

So `grep -rn '"core::option::Option::ok_or' /opt/aeneas/src/extract/ExtractBuiltin*.ml` answers "is
`ok_or` modelled?" directly. What Aeneas REJECTS outright (rather than opaques) is decided in its
pre-passes — `grep -n 'not supported\|unsupported' /opt/aeneas/src/PrePasses.ml /opt/aeneas/src/Main.ml`
shows the constructs it refuses. Prefer signal (1) — the actual output on your actual target — and use
(2) up front to decide whether a dependency is worth keeping, opaquing, or modelling.

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
- **A MODELLED substrate type → translate its VALUE surface only; never delegate a non-value surface
  back to the original.** When you replace an external type with your own model (rung 3), you own it —
  so model only its value-carrying operations. Its non-value trait impls — `Display`/`Debug`/
  formatting, `serde`/serialization, `FromStr`/parsing, `Hash` — must be EXCLUDED (`--exclude`/
  `--opaque` them) or given a trivial self-contained body, **never delegated back to the original
  external type** (`impl Display for MyModel { … OriginalType::from_bits(self.0).fmt(f) }`). Delegation
  re-imports the opaque original, and by the transitive rule above one such "inert formatting" path
  taints every theorem about every function that can reach it — the whole point of modelling the type
  was to keep the original out. Rendering fidelity is worthless for verification; an empty axiom
  footprint is essential. (The one exception: a property that is genuinely ABOUT that surface — then
  model the surface fully too, don't delegate it.)

## Charon / Aeneas mechanics

- **Name matcher — the usual trap:** `--start-from` resolves in the crate being built (the `-p` one),
  so it uses the `crate::` keyword (`crate::module::_::method` for a method). `--opaque`/`--exclude`
  match fully-qualified names, so they use the REAL crate name (`solana_zk_sdk::…`, `core::fmt::…`).
- **One clean invocation (avoids the polluted-tree rejection):** clear the dest and run ONCE,
  WITHOUT `-split-files`, so Aeneas emits a single top-level module `lean/<Crate>.lean` — the layout
  the pipeline expects (the implementation spec then lives at `lean/<Crate>/Spec.lean`):
  `rm -rf /workspace/out/lean/* && aeneas -backend lean -dest /workspace/out/lean <crate>.llbc`.
  Do NOT use `-split-files`: it drops the crate's modules flat at the top level (`Funs.lean`,
  `Types.lean`, …) — several top-level modules, which is rejected as a polluted tree (`-gen-lib-entry`
  adds an entry but leaves the submodules flat; `-subdir` nests them but drops the entry — neither
  gives the expected shape, so just don't split).
- **Trusted primitives to opaque/exclude** (irrelevant to properties): `--exclude core::fmt::Debug::*`,
  `--opaque core::fmt::Formatter`, and crypto/curve/hash/transcript/RNG leaves. For a **crypto/proof
  target**, the ENTIRE external crypto surface — the curve/scalar/point crates and their trait impls,
  hashing, the Fiat–Shamir transcript, the RNG — is one trusted batch: opaque/exclude it wholesale (by
  module/crate, not function-by-function). You verify the algebraic relation the target computes, not
  the primitives; a working example is roughly `--opaque <crate>::transcript --opaque
  <crate>::encryption::pedersen::pedersen_h --opaque core::fmt::Formatter --exclude core::fmt::Debug::*`.
  **If a trusted primitive resists translation** — Aeneas errors or crashes on its signature or body
  (arrow-typed globals/`LazyLock`, `&[u8]`/erased-region crashes, Strobe internals) — **`--opaque` it
  (its whole module); do NOT edit its signature, stub its body, or `cfg`-gate an alternative to force
  it through.** You are assuming the leaf, so assume it; a forced stub silently diverges from the real
  behaviour. Source edits (rung 3) are only for a structure the PROPERTIES depend on.

## Workflow

Plan first, then execute — do NOT grind primitive-by-primitive (that is the failure mode: an agent
that re-examines every dependency in the generated Lean instead of committing).

1. **Plan** (read the target once) and write it to `translate/plan.md` — your anchor: the target's
   own logic is the translatable core; classify every external dependency in ONE pass —
   trusted-and-irrelevant → opaque/exclude as a batch (whole modules/crates); a data structure whose
   contents a property constrains → model (recipe below). Re-read plan.md if you lose the thread; do
   not re-derive it from the generated Lean.
2. **Execute once**: charon with `--start-from` + the whole opaque/exclude batch → aeneas (no
   `-split-files`) → `lake env lean`.
3. **Fix only what actually broke** with a targeted change (a `--start-from` that didn't resolve, a
   target left as a hole/axiom, a compile error) — not another sweep. For a property-relevant
   structure, model it in the source and confirm with `cargo test`. Do not re-open dependencies you
   already classified or study how Aeneas models a primitive.
4. Stop as soon as the target functions are real `def`s and it compiles — the judge + `#print axioms`
   catch a wrong opaque/model call; you don't need to pre-verify every one.
