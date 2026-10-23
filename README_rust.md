# Lusterna

Lusterna is a tool that uses AI agents to formally verify Rust programs.
You can use it with general Rust code, but its main target is Solana programs.

## Overview

> [!NOTE]
> Lusterna is experimental and under active development. Its interface, capabilities, and output can change.

### Stages

The input to Lusterna is a Rust workspace in a git repository and a list of entrypoints.
An entrypoint is a Charon `--start-from` pattern that names the code to translate.
Lusterna runs each stage as a separate Claude Code agent.
If a stage fails its checks, Lusterna runs the stage again, up to `--stage-max-attempts` times.

1. Pre-translate: If the crate does not build on the host with `cargo build`, the agent changes the build until it does.
   If the crate already builds, Lusterna skips this stage.
2. Translate: The agent translates the entrypoints to Lean 4 with [Charon](https://github.com/AeneasVerif/charon)
   and Aeneas, and changes the Rust code where Aeneas cannot translate it.

Lusterna commits the result of each stage to a new branch in a separate git worktree.

## Requirements

- A Rust toolchain with `cargo`
- `lake` from a Lean 4 installation (install it with [elan](https://github.com/leanprover/elan))
- The `claude` CLI, logged in
- A git checkout of Aeneas, with `aeneas` and `charon` built from that checkout

## Installation

```sh
cargo build --release
```

The binary is `target/release/lusterna`.

## Usage

Lusterna needs the Aeneas checkout and the Aeneas and Charon binaries:

1. Give the path of the Aeneas checkout with `--aeneas-root`, or set the `AENEAS_ROOT` environment variable.
2. Put `aeneas` and `charon` on your `PATH`.

```sh
export AENEAS_ROOT=~/src/aeneas
export PATH="$AENEAS_ROOT/bin:$AENEAS_ROOT/charon/bin:$PATH"
```

> [!NOTE]
> The `aeneas` binary on `PATH` must be built from the commit that is checked out in the Aeneas checkout.

Lusterna compares the output of `aeneas -version` with the `HEAD` commit of `AENEAS_ROOT`.
If the two are different, Lusterna stops. Rebuild Aeneas or check out the matching commit.
If the binary was built from a checkout with uncommitted changes, Lusterna shows a warning and continues.
Lusterna also reads the Lean toolchain from `backends/lean/lean-toolchain` in the Aeneas checkout.

Run the translation:

```sh
RUST_LOG=info target/release/lusterna translate /path/to/workspace \
  --crate-root /path/to/workspace/programs/my-program \
  --entrypoint 'crate::module_a' \
  --entrypoint 'crate::module_b' \
  --target-branch lusterna/translate
```

- `--crate-root` selects the crate. If the workspace has more than one crate, you must give it.
- `--entrypoint` is a Charon `--start-from` pattern. You can give it more than once.
- `--target-branch` is the new branch for the result. The branch must not exist yet.
- `--source-branch` is the branch to start from. The default is the checked out branch.
- `--worktree` is where Lusterna creates the worktree. The default is `~/.lusterna/<project>_<source branch>_<target branch>`.
- `--opaque` replaces the default Charon `--opaque` patterns. `--add-opaque` adds patterns to the default patterns.
- `--model` and `--effort` set the Claude model and the effort level.
- `--stage-max-attempts` and `--stage-max-budget` set the attempts per stage and the budget per stage in USD.

Run `lusterna translate --help` for all options.

## Repository layout

```sh
src/
├── main.rs              command-line interface
├── lib.rs               configuration of the agent and the stages
├── agent.rs             runs a Claude Code agent session
├── log.rs               logging per stage
├── stage/
│   ├── mod.rs           runs a stage and repeats it if its checks fail
│   ├── pre_translate/   makes the crate build on the host
│   └── translate/       translates the crate to Lean with Aeneas
└── utils/               cargo, git, and Lean helpers
```

## Security Notice

Lusterna runs the toolchain and the agents on the host directly.
The agents work in a separate git worktree, and Lusterna does not change the checked out branch of the repository.
