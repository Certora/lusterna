---
name: solana-host-build
description: How to make a Solana program build on the host. Use when the crate depends on `solana-program`, `anchor-lang` or another Solana crate.
---

# Solana programs on the host

On sbf, `u128` and `i128` have an alignment of 8 bytes. On the host, they have an alignment of 16 bytes (on arm64, and on x86_64 since Rust 1.77). Many Solana programs check the size or the padding of their account types at compile time. These checks then fail on the host.

Known cases:

| Crate | Failure on the host | Fix |
|---|---|---|
| `static_assertions` | `const_assert_eq!` on sizes in the crate | `#[cfg(target_os = "solana")]` on each call whose type has a `#[repr(...)]` attribute. Without one, `zero_copy(unsafe)` makes the type `repr(packed)`, and the check holds on the host. |
| `bytemuck_derive` (from Anchor `#[account(zero_copy)]`) | `Pod` padding check | Replace `#[account(zero_copy)]` with `#[cfg_attr(target_os = "solana", account(zero_copy))]` and `#[cfg_attr(not(target_os = "solana"), account(zero_copy(unsafe)))]` |
| `ahash` 0.7.6 | `stdsimd` feature | Bump to 0.7.8 in `Cargo.lock` |

Anchor 0.29 `zero_copy(unsafe)` writes `unsafe impl Pod` and `unsafe impl Zeroable` in place of the derive.

Patch the versions that `Cargo.lock` uses. Do not change the `version` line at the top of `Cargo.lock`. The toolchain in `rust-toolchain.toml` can be too old to read a newer format.

Run cargo from the directory of the crate. Cargo then uses the toolchain of the crate's `rust-toolchain.toml`, as Lusterna does.
