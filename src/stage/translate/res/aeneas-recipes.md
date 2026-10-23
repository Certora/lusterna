---
name: aeneas-recipes
description: Rewrites for Rust constructs that Charon or Aeneas cannot translate. Also shows how to find out whether Aeneas has a Lean model for a standard library item. Use when Charon or Aeneas fails on Rust code, or when Aeneas declares an item as an axiom.
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

A `--start-from` pattern resolves in the crate that Charon builds, so it starts with `crate::`. An `--opaque` or `--exclude` pattern uses the real name of the crate, for example `core::fmt::Formatter`.

## Rewrites

Each rewrite must behave exactly like the original for every input.

- `Option` and `Result` combinators: use a `match`. For example, replace `o.ok_or(e)` with `match o { Some(v) => Ok(v), None => Err(e) }`, and `o.map(f)` with `match o { Some(v) => Some(f(v)), None => None }`.
- Iterator adaptor chains: use a `while` loop over an index that writes to a mutable local. For example, replace `v.iter().map(g).sum()` with a loop that adds `g(&v[i])` to an accumulator.
- Early `return`, `break` or `continue` inside a loop: use one exit. Keep the result in a mutable local. Add a flag to the loop condition (`while i < n && !done`). Set the flag where the original leaves the loop.
- `&dyn Trait`: make the function generic (`fn f<T: Trait>(x: &T)`) or use the concrete type.
- Function pointers and globals of function type: use a generic type parameter or a direct call.
- `BTreeMap` or `HashMap`: use a `Vec<(K, V)>`. `get` scans for the key. If the key exists, `insert` updates its entry. Otherwise, `insert` pushes a new entry. If the code depends on the iteration order, do not use this rewrite.

## Mocks

Implement the operations of a mock that the entrypoints reach. If the entrypoints reach a trait impl such as `Display`, `Debug` or a serialization trait, give the mock its own body for it. Do not call the original type from the mock, for example `Original::from_bits(self.0).fmt(f)`, because that brings the original type back into the translation.
