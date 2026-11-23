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

- `git`, `make` and [just](https://github.com/casey/just)
- A Rust toolchain installed with [rustup](https://rustup.rs/)
- [opam](https://opam.ocaml.org/) with an OCaml 5 switch, to build Aeneas
- `lake` from a Lean 4 installation (install it with [elan](https://github.com/leanprover/elan))
- The `claude` CLI, logged in

## Installation

```sh
# Checkout and build Aeneas and Charon 
just setup
# Install `lusterna` using Cargo
just install
```

Note: the installed lusterna binary contains absolute paths into this checkout. If you move or delete the checkout, the binary stops working.

### Updating Aeneas or Solanalib

```sh
just bump-aeneas <rev>
just bump-solanalib <rev>
```

Must be run on a working tree without uncommitted changes

## Usage

### Writing a Spec File

Write the spec of the run to `lusterna.json` in the workspace root:

```json
{
  "entrypoints": ["crate::module_a", "crate::module_b"],
  "add_opaque": [],
  "properties": [],
  "infer": false,
  "design_doc": null
}
```

- `entrypoints` are Charon `--start-from` patterns.
- `add_opaque` adds Charon `--opaque` patterns to the default patterns.
- `properties` are the properties to formalize. With none, the run ends after the translation.
- `infer` infers entrypoints and properties in addition to the given ones.
- `design_doc` can be a single design document that the infer stage uses to find and judge properties. The infer agent will find design relevant docs inside the repository by itself, so only pass this if you want to override it to this one particular document.

### Running Lusterna

Run lusterna:

```sh
cd /path/to/workspace
RUST_LOG=info lusterna run
```

- `run` takes the path of the spec if it is not `lusterna.json` in the workspace root.
- `--workspace-root` selects the workspace. It defaults to the current directory.
- `--crate-root` selects the crate. If the workspace has more than one crate, you must give it.
- The repository must have no uncommitted changes other than the spec.
- Lusterna copies the spec to `lusterna/lusterna.json` and the properties to `lusterna/properties.json` in the crate, and commits them and the result of each stage on the checked out branch of the workspace.
- `--model` and `--effort` set the Claude model and the effort level.
- `--stage-max-attempts` and `--stage-max-budget` set the attempts per stage and the budget per stage in USD.

Run `lusterna run --help` for all options.

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
