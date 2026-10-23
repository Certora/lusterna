---
name: solana-translate
description: Known problems and fixes for the translation of a Solana program with Charon and Aeneas. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Solana programs

## Known problems

Each entry is a fix that worked once. Apply an entry only when its condition holds.

- If Charon fails to link a dependency with "Undefined symbols" (for example `solana-program`), add `--sysroot default` to the `charon` command. If old build output then conflicts, pass a separate `--target-dir` after `--`.
- If the entrypoints reach `Ref` or `RefMut`, the Lean code loses every write through the `RefMut`. An example is `load_mut` of the Anchor `AccountLoader`, or a crate trait that wraps it. Aeneas translates a method that takes `&self` as a function that cannot change `self`, so no Lean definition can fix this. Mock the loader type. In the mock, the loader owns the account value, `load_mut` takes `&mut self` and returns `&mut T`, and `load` returns `&T`. Keep each check that the original makes as a field of the mock. When a check fails, return the same error as the original. For example, if the account is not writable, `load_mut` returns `AccountNotMutable`. If the discriminator does not match, it returns `AccountDiscriminatorMismatch`. Then change the functions that take the loader by `&` to take it by `&mut`.
