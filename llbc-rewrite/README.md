# llbc-rewrite

## Build

This crate builds with the nightly toolchain that Charon pins, because `charon_lib` uses library features that are unstable on stable Rust. `rust-toolchain.toml` selects that toolchain.

rustup reads `rust-toolchain.toml` from the directory that cargo runs in. Run cargo from this directory:

```sh
cd llbc-rewrite
cargo build
```

`cargo build -p llbc-rewrite` from the workspace root uses the stable toolchain and fails.

## rust-analyzer

rust-analyzer runs cargo from the workspace root. To check this crate, add to `.vscode/settings.json`, with the channel from `rust-toolchain.toml`:

```json
"rust-analyzer.cargo.extraEnv": { "RUSTUP_TOOLCHAIN": "nightly-2026-09-17" },
"rust-analyzer.check.extraEnv": { "RUSTUP_TOOLCHAIN": "nightly-2026-09-17" }
```
