---
name: solana-translate
description: Known problems and fixes for the translation of a Solana program with Charon and Aeneas. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Solana programs

## Known problems

Each entry is a fix that worked once. Apply an entry only when its condition holds.

- Before the first run, pass a separate `--target-dir` after `--` in the `charon` command. Without it, the build output of `cargo build` conflicts with the Charon build.
- If the entrypoints reach `Ref` or `RefMut`, the Lean code loses every write through the `RefMut`. An example is `load_mut` of the Anchor `AccountLoader`, or a crate trait that wraps it. Aeneas translates a method that takes `&self` as a function that cannot change `self`, so no Lean definition can fix this. Mock the loader type as follows:
  - Keep all account values in one store that the functions take by `&mut`, with one entry for each account key. A loader is a key or an index into this store. Two loaders for the same account must reach the same entry. An instruction can pass the same account in several places, for example once in a list of accounts and once on its own. Do not copy an account value into a loader.
  - `load_mut` returns `&mut T` into the store, and `load` returns `&T`.
  - Keep each check that the original makes as data in the store. When a check fails, return the same error or panic in the same way. Keep the checks that the original makes when it builds the loader, such as the owner and discriminator checks in `try_from`. Also keep the checks in `load_mut`, such as `AccountNotMutable`.
  - The original can hold two loads of the same account at the same time. Then the second load fails with a borrow error, and the mock must fail in the same way.
  - Add equivalence tests where the same account appears in two places, and tests where each check fails. Compare the account state also after an error.
