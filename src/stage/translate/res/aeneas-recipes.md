---
name: aeneas-recipes
description: Rewrites for Rust constructs that Charon or Aeneas cannot translate, and known problems with their fixes. Also shows how to find out whether Aeneas has a Lean model for a standard library item. Use when Charon or Aeneas fails or crashes, or when Aeneas declares an item as an axiom.
---

# Aeneas recipes

## Find out whether Aeneas models a standard library item

If a standard library item is in the builtin registry of Aeneas, Aeneas has a Lean model for it. Aeneas declares every other item that the entrypoints reach as an `axiom`. The registry is in the Aeneas checkout at `{aeneas_root}`:

- `src/extract/ExtractBuiltin.ml` lists the registered Rust paths as string literals, for example `"core::option::Option"`.
- `src/extract/ExtractBuiltinLean.ml` gives the Lean name of each registered item.

For example, this command shows whether Aeneas models `Option::ok_or`:

```sh
grep -rn '"core::option::Option::ok_or' {aeneas_root}/src/extract/ExtractBuiltin*.ml
```

To see the constructs that Aeneas rejects, run `grep -n 'not supported\|unsupported' {aeneas_root}/src/PrePasses.ml`.

## Pattern names

A `--start-from` pattern resolves in the crate that Charon builds, so it starts with `crate::`. An `--opaque`, `--include` or `--exclude` pattern uses the real name of the crate, for example `core::fmt::Formatter`.

The `rust_fun` attribute above an `axiom` gives the Rust name of the item. Patterns cannot name an inherent impl block. For a method in one, replace the impl block with `_`. For example, the pattern for `core::num::{u64}::abs_diff` is `core::num::_::abs_diff`.

Patterns cannot contain tuple types such as `(_, _)` or array types such as `[_; _]`. For such a pattern, Charon prints `failed to parse pattern` and the run fails. Do not add `--include` for methods of the `Iterator` trait, because Aeneas then crashes on the trait. Write the Lean definitions of these items by hand.

## Rewrites

Each rewrite must behave exactly like the original for every input.

- `Option` and `Result` combinators: use a `match`. For example, replace `o.ok_or(e)` with `match o { Some(v) => Ok(v), None => Err(e) }`, and `o.map(f)` with `match o { Some(v) => Some(f(v)), None => None }`.
- Iterator adaptor chains: use a `while` loop over an index that writes to a mutable local. For example, replace `v.iter().map(g).sum()` with a loop that adds `g(&v[i])` to an accumulator.
- Early `return`, `break` or `continue` inside a loop: use one exit. Keep the result in a mutable local. Add a flag to the loop condition (`while i < n && !done`). Set the flag where the original leaves the loop.
- `&dyn Trait`: make the function generic (`fn f<T: Trait>(x: &T)`) or use the concrete type.
- Function pointers and globals of function type: use a generic type parameter or a direct call.
- `BTreeMap` or `HashMap`: use a `Vec<(K, V)>`. `get` scans for the key. If the key exists, `insert` updates its entry. Otherwise, `insert` pushes a new entry. If the code depends on the iteration order, do not use this rewrite.

## Replacements of types from dependencies

Implement the operations of the replacement that the entrypoints reach. If the entrypoints reach a trait impl such as a serialization trait, give the replacement its own body for it. For `Display` and `Debug`, see the briefing. Do not call the original type from the replacement, for example `Original::from_bits(self.0).fmt(f)`, because that brings the original type back into the translation.

## Known problems

Each entry is a fix that worked once. Apply an entry only when its condition holds.

- The `fixed` crate does not translate. Replace the types of `fixed` that the crate uses, with only the functions that the entrypoints reach.
- Charon can panic with "stack depth limit exceeded" at a closure that uses `TryFromPrimitiveError`. Then the entrypoints reach an enum that derives a `num_enum` trait such as `TryFromPrimitive`. Find that enum in the Charon trace before the panic. Replace its derive with a hand-written `TryFrom` impl, then run again. The hand-written impl must not use `TryFromPrimitive` or `TryFromPrimitiveError`, because Charon panics on these too. Give it an error type of the crate. Do not replace the derive on enums that the entrypoints do not reach.
- If Aeneas fails on a type that the macro `construct_uint!` of the `uint` crate defines, such as `U256`, copy the macro into the crate and rewrite only the parts that fail.
- If Aeneas crashes while it inlines `{promoted_const}` items, add `--inline-anon-consts` to the `charon` command.
- If Charon panics with "stack depth limit exceeded", find the item in its trace output. If that item has an iterator chain with closures, replace the chain with a loop.
- If Aeneas fails on a loop whose body returns early, for example with `?` or `require!`, move the loop unchanged into its own function. Replace that function with a recursive one that handles one element per call and returns the same error at the same element. Do not copy the function that contains the loop. It stays unchanged, except that it calls the new function where the loop was. If the loop is in one branch of an `if` or `match`, this rule also holds.
- If Aeneas fails after `?` on a `Result<&mut T, E>`, replace the function that returns it with one that returns an index or a plain value.
- If Aeneas fails to join the branches of an `if` or `match` because a borrow stays open in one branch, end the borrow before the branch. Copy the value, or move the code that uses the borrow into a helper that returns plain values.
- If Aeneas fails with "Function pointers are not supported yet", the code calls a method through a `&dyn Trait` parameter. Under the feature, make the function generic with one type parameter for each `&dyn Trait` parameter, for example `repay: &R, withdraw: &W` with `R: Trait, W: Trait`. Callers then compile unchanged. Test the generic version against the original, also with two arguments that are the same value.
- If Aeneas fails on a function item passed as an `Fn` value, pass a closure that calls it.
- An `Iterator` instance in `Funs.lean` can have fields that the Lean model of the trait does not have. If `lake build` fails on these fields, add `-filter-trait-methods` to the `aeneas` command.
- If `lake build` fails where a local variable or a parameter has the same name as a module, rename the variable. For example, Lean reads `market.ops.f` as a field of a parameter named `market`.
