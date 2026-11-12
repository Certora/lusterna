---
name: aeneas-specs
description: How Aeneas translates Rust to Lean, and how to state a property of the translation as a triple whose postcondition passes the checks of the formalize stage. Use when you read the extraction or write a theorem in `Spec/`.
---

# Stating properties of an Aeneas translation

## Reading the extraction

- `Types.lean` holds the Rust types as Lean structures and inductives. `Funs.lean` holds the Rust functions. `TypesExternal.lean` and `FunsExternal.lean` hold hand-written definitions of items that Aeneas could not translate.
- The Lean names follow the Rust paths, for example `my_crate.module.Struct.f`. Search with `grep -n` for a definition and read its exact signature before you use it. The examples below leave out the namespaces.
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

For a function `fn f(&mut self, x: u64) -> u64`, Aeneas gives `f (self : Struct) (x : U64) : Result (U64 × Struct)`: the return value and the new `Struct`.

The value of a scalar is `x.val`: a `Nat` for unsigned types and an `Int` for signed types. The elements of a `Vec` or `Slice` are the list `v.val`, and `v.length` is its length.

## The triple

There are two triples. `open Aeneas Lusterna.Spec` gives both notations, and `open Aeneas.Std Result` gives the short names `U64`, `Result` and `ok`, as in the generated modules.

- `f args ⦃ r => post ⦄fail` is `Lusterna.Spec.fspec (f args) (fun r => post)`. It is true when `f args` returns `ok r` and `post` holds for `r`, and also when `f args` returns `fail` or `div`. It states nothing about whether the call panics.
- `f args ⦃ r => post ⦄` is `Aeneas.Std.WP.spec (f args) (fun r => post)`. It is true when `f args` returns `ok r` and `post` holds for `r`. It is false when `f args` returns `fail` or `div`. So it also states that the call does not panic.

Use `⦄fail` for a property about the result of a call or about the state after it. Use `⦃ ⦄` only if the natspec states that the call does not fail. Do not add a hypothesis that rules out the inputs on which the call panics.

Destructure a tuple result in the binder:

```lean
f s x ⦃ (ret, s') => s'.a.val = s.a.val + x.val ⦄fail
```

For a property about several calls, put them in one `do` block and return what the postcondition needs:

```lean
(do let (_, s1) ← f s x; g s1 x) ⦃ (res, s2) => s2.a = s.a ⦄fail
```

## Hypotheses are about the inputs

A hypothesis may constrain the arguments and the state before the call. It must not state anything about the result: that would assume what the theorem should show.

```lean
-- BAD: `h2` assumes the conclusion
theorem t (s s' : Struct) (h : update s = ok s') (h2 : s'.a = s.a) : s'.a = s.a

-- GOOD
theorem t (s : Struct) : update s ⦃ s' => s'.a = s.a ⦄fail
```

## Two kinds of `Result`

- `Aeneas.Std.Result` is the failure monad. A `fail` ends the program.
- `core.result.Result` is the Rust `Result`, an ordinary value that the program returns and branches on. A Rust function that returns `Result<T, E>` and can also panic has the type `… → Aeneas.Std.Result (core.result.Result T E)`.

State what happens in each case of a Rust `Result` with an exhaustive `match` in the postcondition:

```lean
g s x ⦃ (res, s') =>
  match res with
  | .Ok out => out.val = x.val ∧ s'.a.val = s.a.val - x.val
  | .Err _ => s' = s ⦄fail
```

## Keeping the postcondition free of `Result`

The postcondition must not contain a term of type `Aeneas.Std.Result _`. Such a term is a computation that can fail, and a claim about it can hold just because it fails. The harness also looks into the helper `def`s of type `Prop` that the postcondition uses.

Values of every other type are fine. Compare structs, enums, `Option`, `core.result.Result`, keys and lists with `=`, `match` on them, and read their fields.

**Scalar arithmetic.** `x + y` on `U64` has the type `Result U64`, because it fails on overflow. Compute on the values instead:

```lean
-- BAD: `s.a + x` is a `Result U64`
f s x ⦃ (_, s') => s'.a = s.a + x ⦄fail

-- GOOD
f s x ⦃ (_, s') => s'.a.val = s.a.val + x.val ⦄fail
```

**A translated function that you need to state the claim**, for example a getter on the result. Use its model, as the skill `lusterna:aeneas-models` describes.

```lean
-- BAD: `sum s'` is a `Result U64` in the postcondition, and the claim holds when it fails
f s x ⦃ (_, s') => ∀ y, sum s' = ok y → y.val ≤ s'.c.val ⦄fail

-- GOOD: `Struct.sumModel` is the model of `sum`
f s x ⦃ (_, s') => s'.sumModel ≤ s'.c.val ⦄fail
```

**Helper `def`s.** A helper over values and models, such as `def Inv (s : Struct) : Prop := s.c.val ≤ s.getTotal`, is fine. A helper that calls a translated function brings its `Result` into the postcondition.

## No `sorry` in definitions

A `def` whose body is `sorry` can be anything, so a statement that uses it says nothing. Give every helper `def` a real body. The same holds for `axiom` and `opaque`.
