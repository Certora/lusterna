# Solana program semantics

This is appended to the FORMALISE briefing when the verification target is a **Solana program**,
detected structurally from the Solana **runtime ABI** in the source — the account model, the program
entrypoint, the program-error type — NOT from any framework crate. So it holds for a native-Rust
program, an Anchor program, a Pinocchio program, or any other framework: they all implement the same
runtime contract under the same names. It records the platform semantics a faithful spec must respect,
on top of the general Aeneas/Lean rules.

## Two `Result`s, and why they are not the same thing

An Aeneas-translated Rust function that returns `Result<T, E>` and can also panic has type
`… → Aeneas.Std.Result (core.result.Result T E × State)`. TWO distinct `Result`s appear, and
conflating them is the classic error:

- **`Aeneas.Std.Result α`** (`ok | fail | div`) is the **abort monad** — the *metalanguage* effect
  that says "this computation might panic or diverge." It is **unrecoverable**: `bind (fail e) k =
  fail e`, so a failure swallows the continuation. It is how Aeneas represents Rust UB / panic — an
  overflowing `+` that panics, `unwrap` on `None`, `assert!`, non-termination. You see it only as the
  execution hypothesis `f args = ok (res, s')`; **partial correctness EXCUSES it** (a panic means
  nothing ran).
- **`core::result::Result T E`** (`.Ok | .Err`) is an ordinary **value** living *inside* that monad —
  *object-language* data the program itself branches on with `?`, `match`, `unwrap_or`. It is
  **recoverable**: `bind (ok (.Err e)) k = k (.Err e)`, so the `.Err` flows to the continuation, which
  may inspect it and carry on — `fn safe() -> u64 { risky().unwrap_or(0) }` NEVER fails, its `.Err` is
  recovered. This is the `res` you `match .Ok / .Err`.

So the outer `= ok` is *"did it run without aborting"*; the inner `.Ok`/`.Err` is *"what value it
returned."* Name them apart — the **execution** vs the **Rust outcome** — and never call both "Result".

## The third failure layer: the runtime reverts the transaction

Neither of those is the whole story on Solana. When a program's top-level instruction handler returns
a Rust `.Err` (propagated with `?` up to the entrypoint), the **Solana runtime aborts the entire
transaction and RESTORES all account state to what it was before the transaction** — even if the code
already mutated an account in memory before returning the error. Aeneas faithfully threads the
in-memory state, INCLUDING partial mutations on the error path; it does **not** model the revert,
because the revert is an environmental semantic *above* the program.

So there are three genuinely different notions of "failure":

| layer | in Lean | recoverable? | state that PERSISTS |
|---|---|---|---|
| panic / diverge | `fail` / `div` | no (aborts) | pre-state (nothing ran) |
| Rust `.Err`, HANDLED inside the program | `ok (.Err e, s')` then a `match`/`unwrap_or` | yes | whatever the handling code computes |
| Rust `.Err`, PROPAGATED to the entrypoint | `ok (.Err e, s')` at the top level | no (tx reverts) | **pre-state** — the mutated `s'` is discarded |

The trap is the third row: `f = ok (.Err e, s')` at a revert boundary hands you a state `s'` that
Aeneas computed but the chain THROWS AWAY. On that branch `s'` is the function's in-memory scratch,
not the on-chain state.

## Model the on-chain transition, not the raw `s'`

For a top-level instruction handler, state the property over the state that actually PERSISTS:

```lean
-- the state a transaction commits: s' on success, the pre-state on a propagated error or a panic.
def onchain (step : State → Result (core.result.Result T E × State)) (s : State) : State :=
  match step s with
  | ok (.Ok _, s') => s'      -- committed
  | ok (.Err _, _) => s       -- propagated error: reverted, s' discarded
  | _              => s        -- panic / divergence: nothing happened
```

A top-level preservation invariant is then `Inv s → Inv (onchain step s)`, and the failure branches
are **handled** (the persisted state is the pre-state, `Inv s` by hypothesis), not left to an informal
argument. That is what makes the revert **explicit** rather than trusted.

Practical consequences for a `@[lusterna]` spec:

- **State a top-level preservation on the folded transition** — `Inv s → Inv (onchain step s)`.
  Equivalently, you MAY prove `Inv s'` on the `.Ok` branch and leave the `.Err`/panic branches
  unclaimed: that is sound for a **safety** invariant, because a discarded state cannot break safety.
  Either is fine; the fold just makes the reasoning mechanical instead of a note in the margin.
- **Never CHAIN a propagated-`.Err` state into a next step.** `s'` from `f = ok (.Err e, s')` at a
  revert boundary is discarded; the next transaction starts from the pre-state, so a lemma that threads
  that `s'` into a following operation is unsound.
- **A `.Err`-branch claim describes the FUNCTION, not the chain.** If a spec pins the state on a
  Rust-`.Err` branch, it is describing the function's in-memory behaviour; for a handler that partially
  mutates before erroring, that state is NOT what the chain observes. Be clear which you mean.
- **In the REPORT, name the revert as an assumption.** "Preserved on success; the runtime's revert
  makes the failure branches immaterial" — do not claim a fully-mechanised on-chain invariant without
  it, unless the property was stated on the folded transition.

## Arithmetic overflow: the DEPLOYED build decides

The shipped artifact is the **release/BPF build** (`cargo build-sbf` builds the release profile), so
the target's `[profile.release]` — not the dev default — decides whether `+`/`-`/`*` panic-and-revert
on overflow or wrap. Aeneas models each op as fallible or wrapping exactly as the compile Charon ran
had overflow checks on or off, so the model must be built to reproduce the RELEASE posture; the harness
verifies the crate Charon compiled carries the deployment's `[profile.release]`. Two Solana footguns:
the Rust release default is **wrapping** (a program shipped without `overflow-checks = true` wraps on
chain, and a checked model of it would silently over-claim), and a fixed-point/bignum dependency whose
overflow is `debug_assert!`-gated needs its own `[profile.release.package.<crate>] debug-assertions =
true` to check in release. See the fallible-arithmetic reference for the full mechanism.

Out of scope unless a property explicitly constrains it: which accounts a transaction may touch, CPI
trust boundaries, rent/lamports, and the compute budget.
