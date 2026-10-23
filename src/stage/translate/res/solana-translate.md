---
name: solana-translate
description: Known problems and fixes for the translation of a Solana program with Charon and Aeneas. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Solana programs

## Known problems

Each entry is a fix that worked once. Apply an entry only when its condition holds.

- If Charon fails to link a dependency with "Undefined symbols" (for example `solana-program`), add `--sysroot default` to the `charon` command. If old build output then conflicts, pass a separate `--target-dir` after `--`.
