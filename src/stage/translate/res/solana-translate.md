---
name: solana-translate
description: Known problems and fixes for the translation of a Solana program with Charon and Aeneas. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Solana programs

## Known problems

Each entry is a fix that worked once. Apply an entry only when its condition holds.

- Before the first run, pass a separate `--target-dir` after `--` in the `charon` command. Without it, the build output of `cargo build` conflicts with the Charon build.
- Do not mock the Anchor `AccountLoader`, `Ref`, `RefMut` or `Pubkey`. Do not change the Rust code that uses them, except for the `?` rewrite below. The Lean project requires the library `SolanalibAeneas` in `{solanalib_root}/aeneas`. It defines these items:
  - `core.cell.Ref` and `core.cell.RefMut`, with `deref` and `deref_mut`
  - `solana_program.pubkey.Pubkey`, with `eq` and `new_from_array`
  - the type `anchor_lang.accounts.account_loader.AccountLoader`

  Add `public import SolanalibAeneas` to `TypesExternal.lean` and `FunsExternal.lean`. Delete the axioms of the items that it defines. Then define `AccountLoader.load`, `AccountLoader.load_mut` and `key` of the `Key` impl. Use `load`, `loadMut` and `key` in `Solanalib.Aeneas.Anchor.AccountLoader`. The module doc of `{solanalib_root}/aeneas/SolanalibAeneas/Anchor/AccountLoader.lean` shows how.
- If `lake build` fails at `core.result.Result.Insts.CoreOpsTry.branch` on a `core.cell.RefMut` type, the Rust code applies `?` to a `Result<RefMut<_>>`. An example is `loader.get_mut()?`. Aeneas translates the same code with a `match` instead of `?`. For each such `?`, keep the original statement under `#[cfg(not(feature = "lusterna"))]`, and add the statement with a `match` under `#[cfg(feature = "lusterna")]`:

  ```rust
  #[cfg(not(feature = "lusterna"))]
  let mut reserve = reserve.get_mut()?;
  #[cfg(feature = "lusterna")]
  let mut reserve = match reserve.get_mut() {
      Ok(v) => v,
      Err(e) => return Err(From::from(e)),
  };
  ```

  Change nothing else in the statement. If the `?` is inside a larger expression, first move the expression with the `?` into a new `let` in both versions. This rewrite needs no equivalence test.
- Aeneas can fail at a `match` with "Unreachable" or with an error about joining branches. The cause can be a live `RefMut` from `load_mut`, or from a crate method that wraps it. Do not debug this in the Aeneas trace. Move the `load_mut` call and the code that uses the `RefMut` into a new function, so that the `RefMut` ends at the function boundary. Do this for each `RefMut` that is live across a `match`.
- Aeneas translates a method that takes `&self` as a function that cannot change `self`. So the Lean code loses every write through the `RefMut` of `load_mut`, or of a crate method that wraps it. In the final message, list every entrypoint that reaches `load_mut`, or a crate method that wraps it. These entrypoints write through a loader. Two loaders can be the same account. Also list each function that writes through one loader and then reads through another loader of the same account type.
