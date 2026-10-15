# Lusterna

A harness that uses AI agents to formally verify Rust programs. Lusterna infers properties from the sources,
translates the code into Lean 4 using [Aeneas](https://github.com/AeneasVerif/aeneas), states the inferred properties as theorems and proves them.

Although it can be used with general Rust code, its primary use case is the formal verification of Solana programs.

## Overview

The input is a Rust repository and a short text file that tells Lusterna what to focus on.
Lusterna works in multiple stages, each stage spawning a separate AI agent to perform a specific task.
The Python harness runs the stages in order. After each stage, it checks that the agent's results are correct.

### Stages

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

The stages and checks are explained to more detail in [docs/pipeline.md](docs/pipeline.md).

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

[docs/usage.md](docs/usage.md) describes the usage to more detail, including the commands, how to resume a run, incremental runs, and the output files.

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
