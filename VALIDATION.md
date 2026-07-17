# Validation

The shell-driven pipeline has been run end-to-end (through the TRANSLATE gate) on three
targets of increasing difficulty. Each row is a real run against the toolchain image; the
generated artefacts are pulled to the host (gitignored) and the source modifications, when
any, are disclosed in that run's `translate/accountability.md`.

| Target | Soundness rung | Target defs | Holes | Assumed axioms | Judge |
|---|---|---|---|---|---|
| `fibonacci` | SAFE (`--start-from` only) | real `def`s | 0 | 0 | no defects |
| `erc20` | MODEL (BTreeMap ledger → assoc-list) | real `def`s | 0 | 0 | no defects |
| zk-elgamal `zero_ciphertext` (`new` + `verify`) | MODIFICATION | real `def`s | 0 | 31 | no defects |

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

Faithfulness is *disclosed and judged*, not machine-certified: the TRANSLATE-JUDGE rejects a
mocked or behaviour-changing translation, and every source edit is recorded as a diff for
human review. Proof soundness (downstream of TRANSLATE) remains the hard mechanical gate —
`#print axioms` flags any theorem that depends on an assumed axiom as tainted, not verified.
