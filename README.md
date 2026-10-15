# Lusterna

Lusterna is a harness that uses AI agents to formally verify Rust programs.
Although it can be used with general Rust code, it is primarily designed for Solana programs.

## Overview

The agents derive the intended behavior of the code, translate the
code to Lean 4 with [Aeneas](https://github.com/AeneasVerif/aeneas), state the intended behavior as theorems, and prove them.
The agents' output is not trusted but checked: The Lean kernel checks every proof, the Lusterna harness rejects any result that depends on unproven assumptions, and when the agent has to adapt the Rust code so that Aeneas can translate it, a separate agent rejects any change that alters the behavior of the verified functions.

Properties that do not hold are refuted with a machine-checked counterexample, which typically indicates a bug. For
Solana programs, the agents also account for runtime semantics that Aeneas does not model out of the box.

> [!NOTE]
> Lusterna is usable today, but it is experimental and under active development. Its interface, capabilities, and output may change.

### The Lusterna Verification Pipeline

The input to Lusterna is a Rust repository and a short text file that indicates what to focus on.
Lusterna runs the verification end-to-end as a pipeline of stages, each handled by a separate AI agent.
The Lusterna Python tool is the harness that orchestrates the stages. It spawns the agents, waits until they finish and, before moving to the next stage, judges each agent's results for defects. If it finds any, it reiterates the stage instead.

1. **Infer**: Read the source code, choose the functions to verify, and write down the properties
   that these functions are expected to have.
2. **Translate**: Translate the chosen functions to Lean 4 with [Charon](https://github.com/AeneasVerif/charon)
   and [Aeneas](https://github.com/AeneasVerif/aeneas). Code that the properties do not need is left out.
3. **Formalise**: State the properties as Lean 4 theorems (without proofs) and build them with `lake build`,
   iterating until they compile. A separate agent then compares the theorems with the translation and
   the inferred properties. If it finds defects, the theorems are revised.
4. **Prove**: Prove as many theorems as possible. If a proof depends on `sorry` or on axioms other
   than Lean's standard ones, the theorem does not count as proved.
5. **Report**: Write a verification report. The list of proved theorems at the top of the report
   comes from the harness.

The stages and checks are explained in more detail in [docs/pipeline.md](docs/pipeline.md).

## Requirements

- Python 3.10+
- Docker (with access to the Docker daemon)
- An Anthropic API key (`ANTHROPIC_API_KEY`) — forwarded into the container per stage

## Installation

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quickstart

```sh
# 1. Build the toolchain image (one-time — includes the Rust/Lean toolchain + Node + the claude CLI)
lusterna build-image

# 2. Run the pipeline
export ANTHROPIC_API_KEY=sk-...
lusterna run /path/to/rust-repo /path/to/design.md

# The results land as a git branch in the target repo. Review the whole run with:
#   git -C /path/to/rust-repo diff lusterna/<session>-base lusterna/<session>
```

[docs/usage.md](docs/usage.md) describes the usage in more detail, including the commands, how to resume a run, incremental runs, and the output files.

## Repository layout

```sh
lusterna/
├── cli.py          command-line interface
├── pipeline.py     runs the stages in order and checks the result of each stage
├── runner.py       starts an agent session in the container and logs its output
├── briefings.py    the prompt for each stage
├── docs.py         adds the texts from docs/ to the prompts
├── docs/           Aeneas, Lean, and Solana reference texts for the agents
├── lean.py         Lean builds and the checks on theorems and proofs
├── checks/         Lean programs that check the theorem statements
├── container.py    Docker container setup, commands, and export of the result branch
├── checkpoint.py   saves and loads checkpoints
├── tools.py        file and git helpers for the harness
├── schemas.py      shared state passed between the stages
└── config.py       environment variables and logging setup
```

## Security

The toolchain and the AI agent run inside one Docker container. Lusterna mounts no host directories. It copies the target commit into the container and fetches the result branch back out. The container runs with `--cap-drop all` and `--security-opt no-new-privileges`. It has network access, because Cargo downloads dependencies and the agent calls the Anthropic API. Lusterna passes `ANTHROPIC_API_KEY` into the container and does not write it to a file.

See [docs/design.md](docs/design.md#docker-interaction) for details.
