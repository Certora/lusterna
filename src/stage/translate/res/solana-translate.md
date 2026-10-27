---
name: solana-translate
description: Known problems and fixes for the translation of a Solana program with Charon and Aeneas. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Solana programs

## Known problems

Each entry is a fix that worked once. When the condition of an entry holds, apply the entry.

- Before the first run, pass a separate `--target-dir` after `--` in the `charon` command. Without it, the build output of `cargo build` conflicts with the Charon build.
- Do not mock the Anchor `AccountLoader`, `Ref`, `RefMut` or `Pubkey`. Do not change the Rust code that uses them. Only the fixes below for `?`, a live `RefMut` and a `&dyn Trait` parameter can change it. The Lean project requires the library `LusternaLib` in `{lusterna_lean_root}`. It defines these items:
  - `core.cell.Ref` and `core.cell.RefMut`, with `deref` and `deref_mut`
  - `solana_program.pubkey.Pubkey`, with `eq`, `default` and `new_from_array`
  - the type `anchor_lang.accounts.account_loader.AccountLoader`

  Add `public import LusternaLib` to `TypesExternal.lean` and `FunsExternal.lean`. Delete the axioms of the items that it defines. Then define `AccountLoader.load`, `AccountLoader.load_mut` and `key` of the `Key` impl. Use `load`, `loadMut` and `key` in `Lusterna.Anchor.AccountLoader`. The module doc of `{lusterna_lean_root}/LusternaLib/Anchor/AccountLoader.lean` shows how.
- Do not mock these crates: {replaced_crates}. `extract.sh` builds the crate with stubs of them, and their items are opaque. `LusternaLib` defines the Lean models of these items. `lusterna/lean/Extraction/Lusterna.lean` gives them the overflow settings of the release profile of the crate. Do not edit it. These entries replace the entries for the same crates in the skill `lusterna:aeneas-recipes`.
  - Add `public import Extraction.Lusterna` to `TypesExternal.lean` and `FunsExternal.lean`. Delete the axioms of the items that `LusternaLib` defines.
  - Some items stay axioms, because their types name trait structures that Aeneas writes into the crate's own `Types.lean`. Define each one from the method of the trait instance that it gets. For `fixed`, these are `from_num`, `checked_from_num`, `to_num`, `checked_to_num` and `saturating_to_num` of `fixed.types.U68F60`. An example is `def fixed.types.U68F60.from_num {Src : Type} (inst : fixed.traits.ToFixed Src) (src : Src) := inst.to_fixed src`.
  - If the Charon build fails with "lusterna: `fixed!` is not available in the Charon build", the crate uses `fixed!` or a macro of `fixed_macro::types`, for example `fraction!` in `use fixed_macro::types::U68F60 as fraction`. For each use, keep the original expression under `#[cfg(not(feature = "lusterna"))]`, and add `U68F60::from_bits(<bits>)` under `#[cfg(feature = "lusterna")]`. Get `<bits>` from `to_bits()` of the original expression in a test build. Add a test that asserts that both are equal.
  - A stub has only the items that its crate doc in `lusterna-stubs` lists. If the Charon build fails because the crate uses another item, stop and report the item in the final message.
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
- A fix in `lusterna:aeneas-recipes` makes a function generic over a trait in place of a `&dyn Trait` parameter. If the crate implements that trait for the Anchor `AccountLoader`, add `--include` with a pattern for that impl to the `charon` command. Then the Lean code has the instance of the trait for `AccountLoader`, which uses the `AccountLoader` model of `LusternaLib`.
- Aeneas translates a method that takes `&self` as a function that cannot change `self`. So the Lean code loses every write through the `RefMut` of `load_mut`, or of a crate method that wraps it. In the final message, list every entrypoint that reaches `load_mut`, or a crate method that wraps it. These entrypoints write through a loader. Two loaders can be the same account. Also list each function that writes through one loader and then reads through another loader of the same account type.
