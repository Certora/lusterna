# Lusterna

Lusterna formally verifies Rust programs with AI agents.
It takes a natural-language specification, or infers one from the code and its design document, extracts a pure functional model of the code in [Lean 4](https://lean-lang.org/) with [Charon](https://github.com/AeneasVerif/charon) and [Aeneas](https://github.com/AeneasVerif/aeneas), formalizes each property as Lean theorems about the model, and proves each theorem or refutes it with a counterexample.
All proofs are machine-checked by Lean, and Lusterna writes a report that lists the assumptions that each proof depends on.
The main target is Solana programs, but Lusterna also verifies general Rust code.

## Overview

The input to Lusterna is a Rust workspace in a git repository and a spec, a JSON file that lists entrypoints and properties.
Entrypoints are patterns that match the modules/functions to be in scope for verification.
A property is a natural-language requirement of one of two kinds:

- Rule: a functional correctness property of one or more functions. It is formalized as Hoare triples over the extracted functions.
- Invariant: a state invariant over the fields of a struct. It is proven by induction, with a base case for each function that creates the struct and an induction step for each function that mutates it.

Passing a specification is optional, Lusterna can also infer the by itself: an agent derives the properties and the entrypoints from the code and potentially a design document of the crate. Both can be combined: a given spec is kept and and combined with the inferred spec.

The output is a Lean project with the extracted model, the theorems and the proofs, and a report that gives each property one of these verdicts:

- Proven: Lean accepts the proof of every theorem of the property, with no `sorry` and no added axiom. A proof can depend on hypotheses about the pre-state or about code outside the verification scope, which the report lists.
- Refuted: a theorem does not hold, and the report explains the counterexample. This indicates a bug in the code or an incorrect property.
- Unprovable: a theorem cannot be proven due to limitations of the Aeneas extraction.
- Open: the agent found neither a proof nor a counterexample.

### Stages

A Lusterna run is a pipeline of five stages. Each stage takes the output of the stages before it and is executed by a separate agent.
Lusterna checks the output of a stage before the next stage starts. If the checks fail, the agent gets the failures and the stage runs again, up to a maximum number of attempts.

1. Infer: The agent infers entrypoints and properties in addition to those of the spec.
   Lusterna runs this stage only if the spec asks for it.
2. Pre-translate: If the crate does not build on the host with `cargo build`, the agent changes the build until it does.
   If the crate already builds, Lusterna skips this stage.
3. Translate: The agent extracts the Lean 4 model of the verification scope with Charon
   and Aeneas, and faithfully rewrites the Rust code that Aeneas does not support.
4. Formalize: The agent formalizes each property as Lean theorem statements over the extracted model, with the proofs left as `sorry`.
   Lusterna runs this stage only if there are properties.
5. Prove: The agent proves each theorem of the formalize stage, or refutes it with a counterexample.
   Lusterna runs this stage only if there are properties.

After the last stage, Lusterna writes a report of the run.
Lusterna commits the result of each stage and the report on the checked out branch of the repository.

### Status

Lusterna is experimental and under active development. Its interface, capabilities, and output can change.

## Installation

### Requirements

- `git`, GNU Make 4 or newer (on macOS, `brew install make`, which installs it as `gmake`) and [just](https://github.com/casey/just)
- A Rust toolchain installed with [rustup](https://rustup.rs/)
- [opam](https://opam.ocaml.org/) with an OCaml 5 switch, to build Aeneas
- `lake` from a Lean 4 installation (install it with [elan](https://github.com/leanprover/elan))
- The `claude` CLI, logged in.
- macOS or Linux. The formalize and prove stages run their agents in the Claude Code sandbox, which does not run on Windows. On Linux, the sandbox needs [bubblewrap](https://github.com/containers/bubblewrap) and `socat` installed.

### Building and Installing

```sh
# Checkout and build Aeneas and Charon 
just setup
# Install `lusterna` using `cargo`
just install
```

Notes:

- The binary is installed into `~/.cargo/bin`, confirm that it is in `PATH`.
- The installed `lusterna` binary contains absolute paths into this checkout. If you move or delete the checkout, the binary stops working.

### Updating Aeneas or Solanalib

```sh
just bump-aeneas <rev>
just bump-solanalib <rev>
```

Both commands must be run on a working tree without uncommitted changes.

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
- `infer` infers entrypoints and properties in addition to the given ones.
- `properties` are the properties to formalize and prove. With none and without `infer`, the formalize and prove stages do not run.
- `design_doc` can be a single design document, relative to the workspace root, that the infer stage uses to find and judge properties. The infer agent will find design relevant docs inside the repository by itself, so only pass this if you want to override it to this one particular document.

### Running Lusterna

Currently only Claude Code is supported as the backend for Lusterna. You must either be logged in with a subscription (`claude auth login`) or have `ANTHROPIC_API_KEY` set.
With an API key the run is billed per token, so Lusterna prints a warning and asks for confirmation before it starts.
Run lusterna in the workspace root using:

```sh
lusterna run
```

The `run` command optionally takes the path of the spec if it is not `lusterna.json` in the workspace root. The repository must have no uncommitted changes other than the spec.

Optional arguments:

- `--crate-root` selects the crate. If the workspace has more than one crate, you must give it.
- `--allow-dirty`, given before `run`, Lusterna commits the uncommitted changes first, in a commit of their own.
- `--model` and `--effort` set the agent model and the effort level.
- `--stage-max-attempts` and `--stage-max-budget` set the attempts per stage and the budget per stage in USD.
- `--command-timeout` sets the minutes after which a command of the formalize and prove agents is stopped.
- `--yes` runs without asking for confirmation when `claude` is not logged in with a subscription.

Run `lusterna run --help` for all options.

### Output

Lusterna writes its output to the directory `lusterna/` in the crate:

- `lusterna.json` and `properties.json`: the spec and its properties.
- `submissions/`: the submission of each stage that ran, in `infer.json`, `pretranslate.json`, `translate.json`, `formalize.json` and `prove.json`.
- `lean/`: the Lean project with the translation, the theorems and the proofs.
- `report.md`: the report of the run.

### Continuing a Run

If a run was interrupted or failed, continue it:

```sh
lusterna continue
```

- `continue` goes on at the step where the run stopped. A stage that was interrupted goes on from where it stopped, it does not start again.
- `continue` does not remember the options of `run`. Pass `--model`, `--stage-max-budget` and the other options again.
- A crate has one run at a time. `lusterna run` deletes the results of the earlier run and starts a new one.
- `continue` fails if the checked out commit changed since the run stopped.

Continuing a run resets the attempts done by any stage on purpose, you do no need to pass a higher `--stage-max-attempts`, if if the job failed for "max attempts reached".
Contrary, spent budget of an interrupted stage carries over. To continue a run that stopped because a stage reached `--stage-max-budget`, pass a higher `--stage-max-budget`

#### Known Limitations

- A run that stops right after the commit of a step, before Lusterna saved the new commit to `lusterna/.internal/run.json`, cannot be continued. `continue` fails because the checked out commit changed and you need to do a new run (`lusterna run`).
- A run that stops right after a stage started, before the `claude` session of its agent exists, fails on `continue` when it tries to resume that session. In that case, delete `lusterna/.internal/<stage>.state.json` of the stage and run `continue` again.

### Running a Stage Again

To run a stage of the last run again, reset the run to that stage and continue it. This also works after the run finished:

```sh
lusterna reset formalize
lusterna continue
```

The `reset` command takes the stage to run again. It deletes the commits of the later stages and keeps the work of the stage itself as uncommitted changes, which a new agent continues from. It fails if it would delete commits or uncommitted changes that Lusterna did not make.

Optional arguments:

- `--full` deletes the work of the stage too, so that the stage starts from the result of the stage before it.
- `--force` deletes the commits and uncommitted changes that Lusterna did not make, after asking for confirmation.

## Repository layout

```sh
.
├── justfile                 setup, build, test and install commands
├── deps/
│   ├── aeneas/              Aeneas and Charon (git submodule)
│   └── solanalib/           Lean library for Solana programs (git submodule)
├── lusterna-lean/           `LusternaLib`: Lean definitions for the Rust items that Aeneas leaves opaque in Solana programs
├── lusterna-spec/           `LusternaSpec`: Lean checks of the theorems that the formalize stage states
├── lusterna-stubs/          stub crates that replace crates in the Charon build of a Solana crate
├── src/                     the `lusterna` command-line tool: the pipeline, the stages and their agent prompts
└── tests/
    ├── fixtures/demo/       the crate that `just integration-test` runs on
    └── integration/         checks of the output of `just integration-test`
```

## Security Notice

Lusterna runs the toolchain and the agents of the infer, pre-translate and translate stages on the host directly.
The agents of the formalize and prove stages run in the Claude Code sandbox.
The agents work in the repository itself, and Lusterna commits their changes on the checked out branch.
