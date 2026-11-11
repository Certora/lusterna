---
name: aeneas-specs
description: How Aeneas translates Rust to Lean, and how to state a property of the translation as a WP triple whose postcondition passes the checks of the formalize stage. Use when you read the extraction or write a theorem in `Spec/`.
---

# Stating properties of an Aeneas translation

## Reading the extraction

- `Types.lean` holds the Rust types as Lean structures and inductives. `Funs.lean` holds the Rust functions. `TypesExternal.lean` and `FunsExternal.lean` hold hand-written definitions of items that Aeneas could not translate.
- The Lean names follow the Rust paths, for example `my_crate.vault.Vault.deposit`. Search with `grep -n` for a definition and read its exact signature before you use it. The examples below leave out the namespaces.
- Every function returns `Result α`. `Result` is the failure monad of Aeneas: a call returns `ok x`, or `fail` on a panic or an overflow, or `div` if it does not terminate.

| Rust | Lean |
|---|---|
| `u8` … `u128`, `usize` | `U8` … `U128`, `Usize` |
| `i8` … `i128`, `isize` | `I8` … `I128`, `Isize` |
| literal `5u32` | `5#u32` |
| `&T` parameter | `T` parameter |
| `&mut T` parameter | `T` parameter, and the new value of the `T` is part of the result |
| returns `&mut T` | returns `(T × (T → …))`: the value and a function that writes it back |
| `Box<T>` | `T` |
| `Vec<T>` | `alloc.vec.Vec T` |
| `[T; 32]` | `Array T 32#usize` |
| `&[T]` | `Slice T` |
| `Result<T, E>` | `core.result.Result T E`, with `.Ok` and `.Err` |
| `panic!`, overflow, out-of-bounds index | `fail` |

For a function `fn deposit(&mut self, amount: u64) -> u64`, Aeneas gives `deposit (self : Vault) (amount : U64) : Result (U64 × Vault)`: the return value and the new `Vault`.

The value of a scalar is `x.val`: a `Nat` for unsigned types and an `Int` for signed types. The elements of a `Vec` or `Slice` are the list `v.val`, and `v.length` is its length.

## The triple

The notation is scoped in the namespace `Aeneas`, so a file that uses it needs `open Aeneas`. `open Aeneas.Std Result` gives the short names `U64`, `Result` and `ok`, as in the generated modules.

`f args ⦃ r => post ⦄` is `Aeneas.Std.WP.spec (f args) (fun r => post)`. It is true when `f args` returns `ok r` and `post` holds for `r`. It is false when `f args` returns `fail` or `div`. So a triple also states that the call does not panic. If the call may panic for some inputs, add a hypothesis that rules those inputs out.

Destructure a tuple result in the binder:

```lean
deposit v amount ⦃ (ret, v') => v'.total.val = v.total.val + amount.val ⦄
```

For a property about several calls, put them in one `do` block and return what the postcondition needs:

```lean
(do let (_, v1) ← deposit v a; withdraw v1 a) ⦃ (res, v2) => v2.total = v.total ⦄
```

## Hypotheses are about the inputs

A hypothesis may constrain the arguments and the state before the call. It must not state anything about the result: that would assume what the theorem should show.

```lean
-- BAD: `h2` assumes the conclusion
theorem t (v v' : Vault) (h : transfer v = ok v') (h2 : v'.total = v.total) : v'.total = v.total

-- GOOD
theorem t (v : Vault) : transfer v ⦃ v' => v'.total = v.total ⦄
```

## Two kinds of `Result`

- `Aeneas.Std.Result` is the failure monad. A `fail` ends the program, and the triple excludes it.
- `core.result.Result` is the Rust `Result`, an ordinary value that the program returns and branches on. A Rust function that returns `Result<T, E>` and can also panic has the type `… → Aeneas.Std.Result (core.result.Result T E)`.

State what happens in each case of a Rust `Result` with an exhaustive `match` in the postcondition:

```lean
withdraw v amount ⦃ (res, v') =>
  match res with
  | .Ok out => out.val = amount.val ∧ v'.total.val = v.total.val - amount.val
  | .Err _ => v' = v ⦄
```

## Keeping the postcondition free of `Result`

The postcondition must not contain a term of type `Aeneas.Std.Result _`. Such a term is a computation that can fail, and a claim about it can hold just because it fails. The harness also looks into the helper `def`s of type `Prop` that the postcondition uses.

Values of every other type are fine. Compare structs, enums, `Option`, `core.result.Result`, keys and lists with `=`, `match` on them, and read their fields.

**Scalar arithmetic.** `x + y` on `U64` has the type `Result U64`, because it fails on overflow. Compute on the values instead:

```lean
-- BAD: `v.total + amount` is a `Result U64`
deposit v amount ⦃ (_, v') => v'.total = v.total + amount ⦄

-- GOOD
deposit v amount ⦃ (_, v') => v'.total.val = v.total.val + amount.val ⦄
```

**A translated function that you need to state the claim**, for example a getter on the result. Use its model, as the skill `lusterna:aeneas-models` describes.

```lean
-- BAD: `sum s'` is a `Result U64` in the postcondition, and the claim holds when it fails
f s x ⦃ (_, s') => ∀ y, sum s' = ok y → y.val ≤ s'.c.val ⦄

-- GOOD: `Struct.sumModel` is the model of `sum`
f s x ⦃ (_, s') => s'.sumModel ≤ s'.c.val ⦄
```

**Helper `def`s.** A helper over values, such as `def Solvent (v : Vault) : Prop := v.shares.val ≤ v.total.val`, is fine. A helper that calls a translated function brings its `Result` into the postcondition.

## No `sorry` in definitions

A `def` whose body is `sorry` can be anything, so a statement that uses it says nothing. Give every helper `def` a real body. The same holds for `axiom` and `opaque`.
