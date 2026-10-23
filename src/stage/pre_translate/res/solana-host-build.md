---
name: solana-host-build
description: How to make a Solana program build on the host. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Solana programs on the host

On sbf, `u128` and `i128` have an alignment of 8 bytes. On the host, they have an alignment of 16 bytes (on arm64, and on x86_64 since Rust 1.77). Many Solana programs check the size or the padding of their account types at compile time. These checks then fail on the host.

- Size assertions such as `static_assertions::const_assert_eq!(size_of::<T>(), N)` fail. Patch `static_assertions` so that `const_assert!` and `const_assert_eq!` expand to nothing.
- The `Pod` derive of `bytemuck_derive` rejects types with padding. Anchor's `#[account(zero_copy)]` and `#[zero_copy]` use this derive. Patch `bytemuck_derive` so that the `Pod` derive does not emit its padding check.

These two patches fix the checks in the crate and in all of its dependencies. Apply them first. If the build still fails after them, patch other packages.

Patch the versions that `Cargo.lock` uses. Do not change the `version` line at the top of `Cargo.lock`. The toolchain in `rust-toolchain.toml` can be too old to read a newer format.

Run cargo from the directory of the crate. Cargo then uses the toolchain of the crate's `rust-toolchain.toml`, as Lusterna does.
