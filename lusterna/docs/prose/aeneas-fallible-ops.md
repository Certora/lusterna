# How Charon + Aeneas decide whether arithmetic can fail

This note pins down one question that decides whether a proof about the translated code transfers to
the shipping binary: **when does the Lean model treat a Rust `+`/`-`/`*`/`- ` (negation) as a
*fallible* operation that aborts on overflow, and when as a *total* wrapping one?** The answer is not
"always checked" and not "always wrapping" — it is *"whatever the compile Charon ran said"*, and that
has a soundness consequence worth understanding before trusting any arithmetic property.

Everything below is mechanism, verified against the toolchain sources; the citations are exact so you
can re-check them.

## TL;DR

- Rust arithmetic in the model is fallible (returns `Result`, `fail`s on overflow) **iff** the MIR
  Charon read contained the compiler's overflow **assert** — i.e. iff that compile had
  `overflow-checks` / `debug-assertions` on.
- Charon reconstructs this per-operation as `OverflowMode::Panic` vs `OverflowMode::Wrap`; Aeneas
  turns `Panic` into a fallible op and `Wrap` into a total one. **Neither tool hardcodes "checked".**
- The compile Charon runs defaults to the **dev** profile, where overflow checks are **on**. So the
  model is *checked by default, regardless of the target's release profile* — a bias toward the
  faithful case for programs that ship with checks on, and a **silent over-claim** for programs that
  ship with the release default (wrapping).
- Therefore the model is faithful to the deployed binary **only if the overflow behaviour of the
  compile Charon ran matches the overflow behaviour of the deployed build.** Nothing internal
  guarantees this; it is a property of *which crate/profile Charon compiled*.

## The three-link chain

**Link 1 — rustc puts the overflow check in the MIR, or doesn't.** For checked arithmetic, rustc
lowers `a + b` to a `*Checked` binop producing `(result, overflowed)` followed by an
`assert(!overflowed, "attempt to add with overflow")`. That assert is emitted **iff** the compile has
`-C overflow-checks=on` (which cargo derives from the active profile: `debug-assertions`/
`overflow-checks`, both **on** in the `dev` profile and **off** in `release` by default). With checks
off, the same source lowers to a plain wrapping binop and no assert.

**Link 2 — Charon reconstructs `Panic` vs `Wrap` from that assert.**
`charon/src/transform/resugar/reconstruct_fallible_operations.rs` matches a `*Checked` binop against a
following overflow assert:

- assert present → `*binop = binop.with_overflow(OverflowMode::Panic)` and the assert is removed, the
  failure now being part of the binop (`reconstruct_fallible_operations.rs`, the
  addition/subtraction/multiplication case, ~`:585`);
- assert absent (the tuple's overflow bit is unused) → `*binop = binop.with_overflow(OverflowMode::Wrap)`
  (~`:591`).

So Charon's IR carries the overflow semantics **as read off the MIR** — it does not assume, and it
does not consult `Cargo.toml` itself. It faithfully reflects the compile it was handed.

**Link 3 — Aeneas turns the mode into `can_fail`.** `src/symbolic/SymbolicToPureExpressions.ml`
computes each op's effect from the mode: for negation `can_fail = overflow <> OWrap` (~`:542`), for
binops `can_fail = ExpressionsUtils.binop_can_fail binop` (~`:607`). A `can_fail = true` op is
translated monadically — it returns `Result` and `fail`s on overflow (the checked `Std` `add`/`sub`
whose inversion lemmas carry the `< 2^n` / `y ≤ x` bounds); a `can_fail = false` (`OWrap`) op is
total and wraps. **Aeneas honours the mode; it does not hardcode checked either.**

Net: `model op is fallible` ⇔ `Charon saw Panic` ⇔ `the MIR had the assert` ⇔ `that compile had
overflow-checks on`.

## Verified behaviour

Compiling a one-line `pub fn add(a: u64, b: u64) -> u64 { a + b }` crate through Charon four ways and
reading the op back with `charon pretty-print`:

| Charon invocation | emitted op | model |
| --- | --- | --- |
| `charon cargo` (dev default) | `checked.+ … assert(overflow) else panic` | **fallible** |
| `charon cargo -- --release` (release, `overflow-checks` default OFF) | `wrap.+` | **total** |
| `charon cargo -- --release`, manifest has `[profile.release] overflow-checks = true` | `checked.+ … assert … panic` | **fallible** |
| `charon cargo --rustc-arg=-Coverflow-checks=off` | `wrap.+` | **total** |

The op flips with the profile, confirming the chain: the **dev default is checked**, plain `--release`
is **wrapping**, and the release manifest's `overflow-checks` decides the `--release` case. (The
`checked.+ … assert … panic` shown above is the PRE-RESUGAR form, from plain `charon cargo`. The
production `charon cargo --preset=aeneas` runs the `reconstruct_fallible_operations` pass, which
collapses it to `OverflowMode::Panic` — pretty-printed as **`panic.+`** — which Aeneas renders as a
`Result`-returning op. So in a production `.llbc` a checked operator reads `panic.+`, a wrapping one
`wrap.+`.)

## The consequence, and the trap

Because the *compile Charon runs* is what matters, and that compile **defaults to dev (checks on)**,
the model comes out **checked by default no matter what the target ships with**. Two independent facts
must line up for a checked model to be sound:

1. the compile Charon ran had overflow-checks on (usually true by dev default), and
2. the **deployed** artifact also has overflow-checks on (this is the target's *release* profile, and
   for a plain `[profile.release]` it is **off** unless the target opts in).

When (1) holds and (2) does not, the model treats overflow as `fail` (an abort the surrounding proof
excludes) while the real binary **wraps and commits a value**. Every property proved on the success
(`= ok`) path then says nothing about the execution the binary actually runs — an over-claim, and a
silent one.

Two subtleties make this easy to get wrong even on inspection:

- **Per-crate gating.** Whether a given crate's arithmetic panics depends on how *it* is written:
  plain `+`/`-` honour `overflow-checks`; a crate that guards overflow with `debug_assert!` honours
  `debug-assertions` instead. A fixed-point or bignum dependency is often the latter, so confirming
  "checked" for the program's own code is not enough — the dependencies whose arithmetic enters the
  verified core must be checked too.
- **Extraction crates.** When translation goes through a *scoped extraction* (a fresh standalone crate
  holding the target bodies), it is **that crate's** `Cargo.toml` and **that crate's** compile that
  Charon reads — not the original program's manifest. An extraction crate with no `[profile.*]`
  silently inherits the dev default (checked), which may or may not match how the original program is
  deployed. The original program's `overflow-checks` line is evidence about *deployment*, not about
  *what the model computed*.

## What this means when translating

- **The rule: the model must reproduce the deployed build's overflow posture, per crate — always.**
  This is not a judgement call and you do not rely on Charon's dev default *happening* to agree with
  the deployment (it forces everything checked, which is faithful only by coincidence when the target
  also ships checked). Compile Charon **under the deployed profile** so the model reproduces the shipped
  posture by construction — and it stays correct on a target that ships wrapping, where the default
  would silently over-claim.
- The controls (verified above):
  - `charon cargo -- --release` compiles under `[profile.release]`, so the model honours the target's
    real `overflow-checks` and any per-package overrides (e.g. a `fixed`-style
    `[profile.release.package.X] debug-assertions = true`). For an **in-place** translation of the real
    crate this is exact — the model reflects exactly what ships.
  - For a **scoped extraction crate**, the manifest Charon reads is the *extraction* crate's, not the
    target's, and a bare extraction crate has no `[profile.*]`. **Copy the target's deployed
    `[profile.release]` (+ the relevant per-package overrides) into the extraction manifest**, then
    build it the same way, so the extraction reproduces the deployment posture rather than the dev
    default.
  - `charon cargo --rustc-arg=-Coverflow-checks=on|off` forces the posture directly, but it is global
    and cannot reproduce per-package `debug_assert!`-gating, so it is a last-resort fallback, not the
    route to prefer.
- The failure this exists to prevent is a **checked model of a wrapping binary**: the model calls an
  overflow `fail` (an abort the surrounding proof excludes) where the binary wraps and commits a value,
  so every `= ok` property silently misses that execution. A model whose posture differs from the
  deployment is unfaithful, full stop — not something to paper over with `= ok` theorems.
- You can read the outcome directly off the artefacts. In the LLBC, `charon pretty-print` shows
  `panic.+` for a fallible op and `wrap.+` for a total one (or, from a plain non-`--preset=aeneas`
  build, the pre-resugar `checked.+` with an overflow assert). In the emitted
  Lean, a fallible op is a `Result`-returning `add`/`sub` (`… = ok …` in specs, `*_add_inv`/`*_sub_inv`
  bounds in proofs) and a wrapping op is a total function with no `Result`. If arithmetic you expect to
  be checked shows up total (or vice-versa), the compile's overflow posture is not what you assumed.

## Source citations

- Charon overflow-mode reconstruction: `charon/src/transform/resugar/reconstruct_fallible_operations.rs`
  — `OverflowMode::Panic` when a checked binop is followed by a compiler overflow assert, else
  `OverflowMode::Wrap`.
- Aeneas effect from mode: `src/symbolic/SymbolicToPureExpressions.ml` — negation
  `can_fail = overflow <> OWrap`; binops `can_fail = ExpressionsUtils.binop_can_fail binop`.
- Fallible-op models: `backends/lean/Aeneas/Std/Scalar/Ops/{Add,Sub}.lean` (the checked ops that
  `fail` on overflow) and `.../SaturatingOps.lean` (the total saturating variants).
