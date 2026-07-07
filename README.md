# Lusterna

An AI agent that translates Rust programs into formally verified Lean 4 specifications.

Given a Rust repository and a design document, Lusterna:

1. Derives an **abstract specification** from the design document alone — before looking at any code
2. Explores the source code
3. Translates the Rust code to Lean 4 via [Aeneas](https://github.com/AeneasVerif/aeneas)
4. Infers an informal specification from the translated code and the design document
5. Derives a formal Lean 4 specification (theorem stubs with `sorry`)
6. Verifies the spec compiles with `lake build`; iterates until it does (max 3 attempts)
7. Has a spec-judge score the statements against both the implementation and the abstract spec; revises if score < 7
8. **Reconciles** the abstract spec against the implementation spec — classifies discrepancies and flags critical ones (implementation bugs, mis-stated theorems)
9. Estimates proof difficulty per theorem (trivial / moderate / hard-acceptable / likely-misstated)
10. Attempts to fill in proofs for tractable theorems using Lean 4 tactics; after each successful build an embedded proof-judge evaluates progress and stops the stage early if stagnation is detected
11. Writes a final verification report

All generated artefacts are git-committed incrementally inside the toolchain container and pulled to the host on exit.

## Architecture

```
lusterna/
├── cli.py          — Click entry point; manages the container lifecycle
├── agent.py        — Orchestrator agent (pydantic-ai); owns the pipeline loop
├── schemas.py      — Pydantic output schemas shared across stages and subagents
├── subagents.py    — Embedded specialists: effort estimator, context compaction summariser
├── tools.py        — All agent-callable tools (file I/O, Aeneas, Lake, git)
├── docs.py         — Aeneas/Lean skill documents embedded as agent instructions
├── container.py    — Docker lifecycle: start, push repo, exec, pull artefacts, stop
├── git_ops.py      — Git commands run inside the container via docker exec
├── state.py        — AgentDeps: typed dependency bundle injected into every tool
├── checkpoint.py   — Per-session checkpoint directories with incremental numbered files
├── config.py       — All knobs via environment variables
└── logging_setup.py — stdlib logging → stderr; level from LUSTERNA_LOG_LEVEL
```

### Pipeline

```
CLI
 └─ start container
     └─ tar-pipe repo → /workspace/repo
     └─ git init /workspace/out
         └─ Orchestrator agent (pydantic-ai)
             ├─ DOC-INFER    → specs/abstract_informal_spec.json  (git commit)
             │                structured output (design doc only, no code)
             ├─ DOC-FORMALISE → specs/abstract_formal_spec.lean  (git commit)
             │                structured output; orchestrator re-runs DOC-INFER if
             │                ambiguities are flagged (up to 3 rounds)
             ├─ EXPLORE      list_files, read_file
             ├─ TRANSLATE    run_aeneas          → lean/     (git commit)
             │                write_rust_file if Charon/Aeneas errors (up to 2 retries)
             ├─ INFER        → specs/informal_spec.json      (git commit)
             │                structured output from Lean translation + design doc
             ├─ FORMALISE    write_file, check_lean → lean/  (git commit)
             │   + BUILD     (lake build) — loop until pass or 3 attempts
             ├─ SPEC-JUDGE   (re-formalise if score < 7, up to 10 rounds)
             │                reads abstract spec as ground-truth reference
             ├─ RECONCILE    → specs/reconciliation_cycle_N.json  (git commit)
             │                compares abstract spec vs impl spec; classifies discrepancies:
             │                  implementation_wrong / bridge_wrong (CRITICAL)
             │                  abstract_wrong (minor) / design_doc_silent (gap)
             │                accumulates across cycles — critical findings never dropped
             ├─ EFFORT-ESTIMATOR  (subagent; trivial/moderate/hard/misstated per theorem)
             ├─ PROVE        patch_output_lines, check_and_judge
             │                attempts only trivial+moderate theorems; after each
             │                successful build an inline PROOF-JUDGE runs and PROVE
             │                stops immediately if stagnant or no sorry remain
             └─ REPORT       write_file          → VERIFICATION_REPORT.md
 └─ tar-pipe /workspace/out → host out_dir
 └─ stop container
```

### Docker interaction

The toolchain (Rust/Cargo, Charon, Aeneas, Lean/Lake) lives entirely inside a Docker container. There are no bind-mounts: the source repo is pushed in via a tar pipe at session start and artefacts are pulled back out at the end. This means:

- The container has its own isolated filesystem — no host paths are exposed
- The agent cannot reach anything outside `/workspace/repo` (read-only by convention) or `/workspace/out` (writable)
- The container runs with `--network none`, `--cap-drop all`, and `--security-opt no-new-privileges`

All toolchain invocations go through `docker exec`. Git also runs inside the container so the commit history is part of the pulled artefacts.

### Pipeline stages (`agent.py`)

Each stage is a full `Agent` run driven by the Python pipeline loop in `run_session()`. Each stage starts with a fresh context — stages communicate via the filesystem (git-committed artefacts) and `deps.progress`, not via message history. Within each stage, a manual compaction step triggers when accumulated input tokens exceed `LUSTERNA_COMPACTION_THRESHOLD`: all messages up to the start of the last complete turn are summarised by a lightweight subagent and replaced with a single summary message.

| Stage | Key tools | Purpose |
|---|---|---|
| DOC-INFER | *(structured output)* | Derive abstract informal spec from design doc — no code access |
| DOC-FORMALISE | *(structured output)* | Derive abstract Lean stubs; orchestrator re-runs DOC-INFER if ambiguities are flagged (up to 3 rounds) |
| EXPLORE | `list_files`, `read_file` | Survey the Rust source; flag Aeneas incompatibilities |
| TRANSLATE | `run_aeneas`, `write_rust_file` | Charon → Aeneas → Lean; massage Rust on errors |
| INFER | *(structured output)* | Read Lean translation + design doc; return structured InformalSpec |
| FORMALISE | `write_file`, `check_lean` | Derive theorem stubs from informal spec; iterate until `lake build` passes |
| SPEC-JUDGE | `read_output_file`, `get_build_result` | Score statements against impl spec and abstract spec; re-formalise if score < 7 |
| RECONCILE | `read_output_file`, `list_files` | Compare abstract vs impl spec; classify discrepancies; accumulate across cycles |
| PROVE | `patch_output_lines`, `check_and_judge` | Fill `sorry` proofs for trivial/moderate theorems only; inline proof-judge after each successful build detects stagnation early |
| REPORT | `read_output_file`, `git_log` | Produce `VERIFICATION_REPORT.md` |

### Embedded specialists (`subagents.py`)

Specialists are invoked as tool calls from within a pipeline stage. They receive no message history, return structured Pydantic output to the calling stage, and are invisible to the pipeline loop.

| Specialist | Output type | Purpose |
|---|---|---|
| effort-estimator | `TheoremEstimate` | Classifies each theorem as trivial / moderate / hard-acceptable / likely-misstated before PROVE runs |
| proof-judge | `ProofVerdict` | Classifies each theorem as proved / sorry-acceptable / likely-misstated; runs inline after each successful `lake build` during PROVE |

### Proof search approach

Before PROVE runs, an effort-estimator subagent reads the formal spec and classifies every theorem by difficulty. PROVE then attempts only the `trivial` and `moderate` theorems, leaving `hard_acceptable` ones as `sorry` immediately. This avoids burning tokens on proofs that require advanced techniques beyond automation.

PROVE uses `check_and_judge` (`lake build` + inline proof-judge) as its feedback mechanism — no interactive LSP, no retrieval-augmented generation. After each successful build the inline proof-judge evaluates the current state and PROVE stops immediately if all remaining `sorry` theorems are classified as acceptable or if progress has stagnated. The stage agent works from its training knowledge of Lean 4 and Aeneas idioms (embedded as skill documents in `docs/`). This keeps the toolchain simple and avoids the latency and reliability problems of running a Lean language server inside a locked-down, network-isolated container.

### Checkpoints

After every pipeline stage that mutates state the agent saves a checkpoint. Checkpoints are stored as numbered JSON files inside a per-session directory:

```
~/.local/share/lusterna/sessions/
  <session-id>/
    checkpoint-001.json   — after TRANSLATE
    checkpoint-002.json   — after INFER
    checkpoint-003.json   — after FORMALISE
    ...
```

Each file records:

- `state` — progress dict, container ID, repo/work paths, design doc
- `git_head` — SHA of `/workspace/out` HEAD at save time

The `git_head` field lets you reset the output repo to the exact git state that matches any given checkpoint before resuming. Since each stage starts with fresh context, resuming simply skips completed stages (tracked via the progress dict) and re-runs from the first incomplete one.

## Requirements

- Python 3.10+
- Docker (with access to the Docker daemon)
- An Anthropic API key (`ANTHROPIC_API_KEY`)

## Installation

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quickstart

```sh
# 1. Build the toolchain image (one-time, ~30 min for the real image)
lusterna build-image

# 2. Run the pipeline
export ANTHROPIC_API_KEY=sk-...
lusterna run /path/to/rust-repo /path/to/design.md

# Artefacts land in /path/to/rust-repo-lusterna/ by default.
# Override with --out:
lusterna run /path/to/rust-repo /path/to/design.md --out /tmp/results
```

## Commands

```
lusterna [--verbose] COMMAND
```

### `run`

```
lusterna run REPO DESIGN_DOC [OPTIONS]
```

Run the full verification pipeline on `REPO` using `DESIGN_DOC`.

| Option | Default | Description |
|---|---|---|
| `--out DIR` | `<repo>-lusterna/` | Host directory to pull artefacts into |
| `--session-id ID` | (new UUID) | Resume a previous session |
| `--checkpoint-number N` | (latest) | Checkpoint to resume from within a session |
| `--container NAME` | (auto-start) | Attach to a pre-running toolchain container |
| `--image TAG` | `lusterna-toolchain:latest` | Image to start when `--container` is not given |
| `--token-budget N` | (unlimited) | Maximum total tokens across all agents for this session; overrides `LUSTERNA_TOKEN_BUDGET`; 0 = unlimited |

Output (stdout, JSON):

```json
{
  "session_id": "...",
  "out_dir": "/path/to/rust-repo-lusterna",
  "container_id": "...",
  "summary": "...",
  "progress_keys": ["aeneas", "informal_spec", "formal_spec", "lean_build", "verdict", "proof_verdict"]
}
```

Progress and errors go to stderr via structured log lines.

### `build-image`

```
lusterna build-image [--tag TAG]
```

Build the `lusterna-toolchain` Docker image from the project `Dockerfile`. Required before the first `run`.

### `list-sessions`

```
lusterna list-sessions
```

Print all session IDs that have at least one checkpoint, with a summary of their latest state.

### `list-checkpoints`

```
lusterna list-checkpoints SESSION_ID
```

Print all checkpoints for `SESSION_ID` as JSON, including their number, timestamp, `git_head` SHA, progress keys, and message count.

### `show-checkpoint`

```
lusterna show-checkpoint SESSION_ID [--number N]
```

Print the state JSON for a specific checkpoint (default: latest).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Anthropic API key |
| `LUSTERNA_MODEL` | `anthropic:claude-sonnet-4-6` | Model for the orchestrator and most subagents |
| `LUSTERNA_JUDGE_MODEL` | `anthropic:claude-sonnet-4-6` | Model for the judge subagent |
| `LUSTERNA_SESSIONS_DIR` | `~/.local/share/lusterna/sessions` | Root directory for per-session checkpoint directories |
| `LUSTERNA_AENEAS_BIN` | `aeneas` | Aeneas binary name inside the container |
| `LUSTERNA_LAKE_BIN` | `lake` | Lake binary name inside the container |
| `LUSTERNA_IMAGE` | `lusterna-toolchain:latest` | Default Docker image |
| `LUSTERNA_CONTAINER` | — | Pre-existing container to attach to (skips auto-start) |
| `LUSTERNA_TOKEN_BUDGET` | (unlimited) | Maximum total tokens across all agents for a session; 0 or unset = unlimited |
| `LUSTERNA_REQUEST_LIMIT` | (unlimited) | Maximum model requests per pipeline stage; 0 or unset = unlimited |
| `LUSTERNA_COMPACTION_THRESHOLD` | `500000` | Compact within-stage context when accumulated input tokens reach this value |
| `LUSTERNA_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Resuming a session

Each pipeline stage saves a numbered checkpoint. Use `list-checkpoints` to inspect them, then resume from any point.

```sh
# See all sessions
lusterna list-sessions

# Inspect checkpoints for a session
lusterna list-checkpoints <session-id>
# [
#   {"number": 1, "git_head": "d469df7e...", "progress_keys": ["aeneas"]},
#   {"number": 2, "git_head": "d463a94c...", "progress_keys": ["aeneas", "informal_spec"]},
#   ...
# ]

# Resume from the latest checkpoint (default)
lusterna run /path/to/repo design.md --session-id <uuid> --out /path/to/out

# Resume from a specific checkpoint
# First reset the output repo git state to match:
git -C /path/to/out reset --hard <git_head from checkpoint N>
# Then resume:
lusterna run /path/to/repo design.md --session-id <uuid> --checkpoint-number N --out /path/to/out
```

When resuming, if the recorded container is no longer running a fresh one is started automatically and any existing artefacts in `--out` are pushed back into it before the agent continues.

## Output artefacts

After a run, `<out_dir>/` contains a git repository with one commit per pipeline stage:

```
<out_dir>/
├── lean/
│   ├── Foo.lean              — Aeneas translation
│   ├── FooSpec.lean          — Formal specification (theorem stubs + proofs)
│   ├── lakefile.lean         — Lake project file
│   └── lake-manifest.json    — Pre-resolved package manifest (offline)
├── specs/
│   ├── abstract_informal_spec.json   — Abstract spec from design doc (no code)
│   ├── abstract_formal_spec.lean     — Abstract Lean stubs (design intent)
│   ├── informal_spec.json            — Implementation informal spec
│   ├── formal_spec.lean              — Raw formal spec from the formaliser subagent
│   └── reconciliation_cycle_N.json   — Discrepancies between abstract and impl spec (one per cycle)
├── report/
│   ├── 01_overview.md                — General overview
│   ├── 02_translation.md             — Aeneas/Charon translation report
│   └── ...                           — all individual sections composing VERIFICATION_REPORT.md
└── VERIFICATION_REPORT.md    — Final report: theorem status, discrepancies, proof sketches
```

```sh
git -C <out_dir> log --oneline
# 0da6a37 stage/report: final pipeline report
# 524bf30 stage/formalise: FibonacciSpec.lean — improved spec
# ad4435c stage/formalise: FibonacciSpec.lean with theorem stubs
# 71e70e0 feat(spec): formal specification stubs
# ae3521a stage/infer: informal specification
# d463a94 feat(spec): informal specification
# d469df7 feat(aeneas): translate src/main.rs → Lean
# ...      chore: init lusterna session
```

## Development

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e . pytest

# Use the fast stub image (aeneas and lake are shell scripts that return immediately)
docker build -f Dockerfile.test -t lusterna-toolchain:latest .

# Run against the included example
ANTHROPIC_API_KEY=sk-... lusterna run tests/fibonacci tests/fibonacci/DESIGN.md
```

The `Dockerfile` (as opposed to `Dockerfile.test`) installs the real toolchain: Rust via `rustup`, [Charon](https://github.com/AeneasVerif/charon) from its nightly release, Aeneas built from source with Lake, and Lean 4 via `elan`. Building it takes approximately 30 minutes and several gigabytes of disk space.
