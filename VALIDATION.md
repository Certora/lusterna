# Validation

The shell-driven pipeline has been run against real targets of increasing difficulty. The first
three go through the **full** pipeline (TRANSLATE → FORMALISE → SPEC-JUDGE → PROVE → `#print axioms`
→ REPORT); the fourth (`metavault`, a real-world Anchor/Solana program) has been driven through the
spec stages (see below). Each row is a real run against the toolchain image; the generated artefacts
are pulled to the host (gitignored) and the source modifications, when any, are disclosed in that
run's `translate/accountability.md`.

| Target | Soundness rung | Target defs | Holes | Assumed axioms | Judge |
|---|---|---|---|---|---|
| `fibonacci` | SAFE (`--start-from` only) | real `def`s | 0 | 0 | no defects |
| `erc20` | MODEL (BTreeMap ledger → assoc-list) | real `def`s | 0 | 0 | no defects |
| zk-elgamal `zero_ciphertext` (`new` + `verify`) | MODIFICATION | real `def`s | 0 | 31 | no defects |
| `metavault` (Kamino meta-vault, Anchor 0.29 / Solana 1.17) | MODIFICATION (shim-decouple + refactors) | real `def`s (29 core ops) | 0 | ~138 (framework/account + opaque numeric) | no defects |

**Full pipeline — `fibonacci`.** Beyond TRANSLATE, `fibonacci` has been carried all the way to a
proved spec: **9 theorems, all kernel-established with only the standard axioms** — the two base
cases, the two-step recurrence, exact agreement with the mathematical Fibonacci number over the
representable range, monotonicity, and `Err`-on-overflow at F(94). (The `#print axioms` gate does its
job: a run where PROVE reached for `native_decide` on a bound was correctly reported *tainted*, not
established.)

**What each rung means.** *SAFE* scopes to the target's call-closure and translates it
verbatim. *MODEL* additionally refactors, in the source, a data structure the properties
depend on into one Aeneas has a Lean model for (confirmed against the crate's own
`cargo test`). *MODIFICATION* additionally applies behaviour-preserving source refactors to
route around named Aeneas fragment limits.

**zk-elgamal notes.** The 31 assumed axioms are the trusted external crypto surface —
`RistrettoPoint`/`Scalar` and their arithmetic, the Merlin transcript, `multiscalar_mul`,
the Pedersen `H` generator — opaqued wholesale as leaves the soundness property treats as
abstract tokens (never a target function, never a property-relevant structure). The
MODIFICATION rung covers four behaviour-preserving refactors, each around a specific Aeneas
limitation and each disclosed in `translate/accountability.md`: a `get_pedersen_h()` getter
(arrow-typed `LazyLock` global), per-use transcript wrapper methods (`&'static [u8]` label
unsize-coercion), an explicit-arity `multiscalar_mul` wrapper (`Vec<&T>` erased-region
crash), and a flattened algebraic-relation wrapper (deep post-`?` nesting). The agent
declined an unsafe `transmute` shortcut as non-behaviour-preserving and opaqued instead.

The minimal-translatable-target scoping in INFER — forming the smallest genuinely
translatable target set (the soundness core `new` + `verify`, excluding serialization
plumbing) before TRANSLATE plans — was what let the zk target converge in one planned pass
rather than grinding dependency-by-dependency.

**metavault notes (real-world scale).** The fully-vendored Kamino meta-vault is the hardest target
run so far. The agent drove it unaided: EXPLORE empirically assessed the toolchain (build
prerequisites under the bundled nightly; the klend/kfarms/scope account+oracle layer as the E1–E4
trust boundary; the fixed-point/bignum numeric layer as something to model); INFER scoped to the
~29-function share/value-accounting core; TRANSLATE built shim crates to decouple from the
uncompilable protocol tree and applied behaviour-preserving refactors, reaching a compiling,
judge-approved translation (0 holes); FORMALISE → SPEC-JUDGE produced a **38-theorem, fully-compiling
structural spec** — the central economic theorem I7 (no round-trip extraction, at conversion and
end-to-end levels), the share-accounting equation (I1), the §3 directional-rounding lemmas, and the
arithmetic-safety obligations (A1–A4).

It is **structural, not yet proved**: klend's `Fraction` (`= fixed::U68F60`) and `uint::U256` stay
*opaque* (Aeneas cannot translate the bit-level fixed-point/bignum libraries), so the statements
compile and reference the real translated ops but the arithmetic underneath is assumed — I7 and the
rounding *inequalities* are stated but not provable, which SPEC-JUDGE surfaces as a residual
`too_weak` on I7. Making them provable requires modelling the numeric layer (a rational / `Nat`
model the translation maps onto) — the next frontier; PROVE was not run. Artefacts:
`demo/metavault-private/docs/fv-campaign/` (gitignored).

Faithfulness is *disclosed and judged*, not machine-certified: the TRANSLATE-JUDGE rejects a
mocked or behaviour-changing translation, and every source edit is recorded as a diff for
human review. Proof soundness (downstream of TRANSLATE) remains the hard mechanical gate —
`#print axioms` flags any theorem that depends on an assumed axiom as tainted, not verified.
