# Lusterna

A harnessed orchestrator for AI Agents to translate Rust programs into formally verified Lean 4 specifications.

Lusterna **spawns an AI agent as the engine for each pipeline stage** — a headless agent
session, running inside the toolchain container, drives Charon/Aeneas/Lake and does all the file
and proof work itself. The Python harness is a thin, **trusted spine**: it sequences the stages and
runs the soundness gates that the agents are not allowed to touch.

Given a Rust repository and an instruction document with a minimal set of hints, Lusterna:

1. **Infers**, from the pristine code, the behavioural properties the target functions satisfy, which
   functions the verification targets, and the minimal state the properties constrain (the instruction
   document is only a focus hint; the code is the source of truth). This is the first stage — it also
   orients: the entry crate and the functions that matter.
2. **Translates** the target to Lean 4 via [Charon](https://github.com/AeneasVerif/charon) +
   [Aeneas](https://github.com/AeneasVerif/aeneas), running the toolchain reality-check itself and
   owning the build-vs-extract strategy — by default lifting only the minimal core into a scoped
   extraction crate (never mocking the target away)
3. **Formalises** the properties as Lean 4 theorem statements and builds them with `lake build`,
   iterating until they compile
4. Has a **spec-judge** list concrete defects in the theorem statements (checked against the
   translation and the inferred properties); revises until the list is empty
5. **Proves** as many statements as it can with Lean 4 tactics against the `lake build` oracle
6. Runs **`#print axioms`** — the authoritative gate: a theorem is *established* only if its proof
   depends on nothing beyond the standard axioms (so an untranslated hole, a leftover `sorry`,
   `native_decide`'s compiler trust, or an assumed axiom all leave it reported as *tainted*, not
   verified)
7. Writes a **verification report** — led by a harness-generated verdict block that no agent
   narrative can override.

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

```
lusterna/
├── cli.py          — Click entry point; manages the container lifecycle
├── pipeline.py     — The spine: per-stage functions that spawn an AI-agent session and apply
│                     the trusted gate (the _cc_gate_loop / PROVE best-loop), + run_session
├── runner.py       — run_cc_stage: launch `claude -p` headless in-container and stream its
│                     stream-json activity to the host log; mint/resume the session id
├── briefings.py    — The task briefing per stage (the spawn-model prompt library)
├── lean.py         — Aeneas/Lean domain logic: translation analysis, lake build, the
│                     collectAxioms gate (check_axioms), the MetaM check drivers, spec operations
├── checks/         — harness-owned Lean SOURCE: spec_checks.lean (the MetaM checks) +
│                     spec_schemas.lean (the @[lusterna_*] schema attributes), shipped per crate
├── tools.py        — The harness's own file/git IO helpers inside the container (not agent tools)
├── schemas.py      — AgentDeps (the shared dependency handle)
├── docs.py         — Aeneas/Lean skill documents appended to the relevant briefings
├── container.py    — Docker lifecycle: start, push repo, exec, exec_stream, export the run branch
├── checkpoint.py   — Per-session numbered checkpoints + snapshot(deps) serialisation
└── config.py       — Env-driven knobs, plus logging setup
```

## Security

The toolchain and the AI agent run inside one Docker container. Lusterna mounts no host directories. It copies the target commit into the container and fetches the result branch back out. The container runs with `--cap-drop all` and `--security-opt no-new-privileges`. It has network access, because Cargo downloads dependencies and the agent calls the Anthropic API. Lusterna passes `ANTHROPIC_API_KEY` into the container and does not write it to a file.

See [docs/design.md](docs/design.md#docker-interaction) for details.
