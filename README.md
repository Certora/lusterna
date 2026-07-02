# Lusterna

An AI agent that translates Rust programs into formally verified Lean 4 specifications.

Given a Rust repository and a design document, Lusterna:

1. Explores the source code
2. Translates the Rust code to Lean 4 via [Aeneas](https://github.com/AeneasVerif/aeneas)
3. Infers an informal specification from the translated code and the design document
4. Derives a formal Lean 4 specification (theorem stubs with `sorry`)
5. Verifies the spec compiles with `lake build`; iterates until it does (max 3 attempts)
6. Has a spec-judge subagent score the statements; revises if score < 7
7. Attempts to fill in proofs using Lean 4 tactics
8. Has a proof-judge subagent classify each theorem (proved / sorry-acceptable / misstated)
9. Writes a final verification report

All generated artefacts are git-committed incrementally inside the toolchain container and pulled to the host on exit.

## Architecture

```
lusterna/
├── cli.py          — Click entry point; manages the container lifecycle
├── agent.py        — Orchestrator agent (pydantic-ai); owns the pipeline loop
├── subagents.py    — Specialist subagents: spec inferrer, formaliser, judge, summariser
├── tools.py        — All agent-callable tools (file I/O, Aeneas, Lake, Mathlib search, git)
├── docs.py         — Aeneas/Lean skill documents embedded as agent instructions
├── container.py    — Docker lifecycle: start, push repo, exec, pull artefacts, stop
├── git_ops.py      — Git commands run inside the container via docker exec
├── state.py        — AgentDeps: typed dependency bundle injected into every tool
├── checkpoint.py   — Per-session checkpoint directories with incremental numbered files
├── compaction.py   — Context summarisation when the token estimate exceeds the threshold
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
             ├─ EXPLORE      list_files, read_file
             ├─ TRANSLATE    run_aeneas          → lean/     (git commit)
             │                write_rust_file if Charon/Aeneas errors (up to 2 retries)
             ├─ INFER        infer_spec          → specs/    (git commit)
             │                └─ spec-inferrer subagent
             ├─ FORMALISE    formalise_spec      → lean/     (git commit)
             │   + BUILD     check_lean (lake build) — loop until pass or 3 attempts
             │                └─ formal-spec subagent
             ├─ SPEC-JUDGE   judge subagent      (re-formalise if score < 7, up to 10 rounds)
             ├─ PROVE        write_file, check_lean, search_mathlib
             │                (fill sorry proofs with tactics; up to 80 messages)
             ├─ PROOF-JUDGE  proof-judge subagent
             │                (proved / sorry-acceptable / misstated; feeds misstated back)
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

### Subagents

| Agent | Output type | Purpose |
|---|---|---|
| `_spec_inferrer` | `InformalSpec` | Reads Lean + design doc → preconditions, postconditions, invariants, edge cases |
| `_formal_spec_writer` | `FormalSpec` | Turns informal spec → Lean 4 definitions and theorem stubs |
| `_judge` | `JudgeVerdict` | Scores the formal spec 0–10 per component; lists gaps and suggestions |
| `_proof_judge` | `ProofVerdict` | Classifies each theorem as proved / sorry-acceptable / likely-misstated |
| `_summariser` | `str` | Condenses message history when the context threshold is reached |

### Proving tools

The PROVE stage has access to:

- `check_lean` — runs `lake build` inside the container and returns stdout/stderr; the primary feedback loop for proof development
- `search_mathlib` — queries [Loogle](https://loogle.lean-lang.org) by name fragment or type signature to find relevant Mathlib lemmas (runs outside the air-gapped container)
- `read_output_file`, `write_file`, `git_commit` — read/write Lean files and commit progress

No interactive LSP or RAG retrieval is used during proof search. The model works from its training knowledge of Lean 4 and Aeneas idioms (embedded as skill documents in `docs/`), supplemented by on-demand Mathlib searches. This keeps the toolchain simple and avoids the latency and reliability problems of running a Lean language server inside a locked-down container.

### Context management

Token usage is estimated with a character-based heuristic (4 chars ≈ 1 token). When the estimate exceeds `LUSTERNA_COMPACTION_THRESHOLD` (default 80 000 tokens), the summariser subagent condenses all but the last two messages into a single system prompt entry, keeping the orchestrator coherent across long sessions.

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
- `messages` — full pydantic-ai conversation history serialised with `ModelMessagesTypeAdapter`

The growing message history means the agent resumes with complete conversational context — it remembers every tool call, error, and decision from the previous run. The `git_head` field lets you reset the output repo to the exact git state that matches any given checkpoint before resuming.

## Requirements

- Python 3.10+
- Docker (with access to the Docker daemon)
- An Anthropic API key (`ANTHROPIC_API_KEY`)

## Installation

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Quickstart

```sh
# 1. Build the toolchain image (one-time, ~30 min for the real image)
.venv/bin/lusterna build-image

# 2. Run the pipeline
export ANTHROPIC_API_KEY=sk-...
.venv/bin/lusterna run /path/to/rust-repo /path/to/design.md

# Artefacts land in /path/to/rust-repo-lusterna/ by default.
# Override with --out:
.venv/bin/lusterna run /path/to/rust-repo /path/to/design.md --out /tmp/results
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
| `LUSTERNA_COMPACTION_THRESHOLD` | `80000` | Estimated token count that triggers context compaction |
| `LUSTERNA_SESSIONS_DIR` | `~/.local/share/lusterna/sessions` | Root directory for per-session checkpoint directories |
| `LUSTERNA_AENEAS_BIN` | `aeneas` | Aeneas binary name inside the container |
| `LUSTERNA_LAKE_BIN` | `lake` | Lake binary name inside the container |
| `LUSTERNA_IMAGE` | `lusterna-toolchain:latest` | Default Docker image |
| `LUSTERNA_CONTAINER` | — | Pre-existing container to attach to (skips auto-start) |
| `LUSTERNA_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Resuming a session

Each pipeline stage saves a numbered checkpoint. Use `list-checkpoints` to inspect them, then resume from any point.

```sh
# See all sessions
lusterna list-sessions

# Inspect checkpoints for a session
lusterna list-checkpoints <session-id>
# [
#   {"number": 1, "git_head": "d469df7e...", "progress_keys": ["aeneas"], "messages": 7},
#   {"number": 2, "git_head": "d463a94c...", "progress_keys": ["aeneas", "informal_spec"], "messages": 13},
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
│   ├── FooSpec.lean          — Formal specification (theorem stubs)
│   ├── lakefile.lean         — Lake project file
│   └── lake-manifest.json    — Pre-resolved package manifest (offline)
├── specs/
│   ├── informal_spec.json    — Structured informal specification
│   └── formal_spec.lean      — Raw formal spec from the formaliser subagent
└── VERIFICATION_REPORT.md    — Final report: theorem status, open obligations, proof sketches
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
.venv/bin/pip install -e . pytest

# Use the fast stub image (aeneas and lake are shell scripts that return immediately)
docker build -f Dockerfile.test -t lusterna-toolchain:latest .

# Run against the included example
ANTHROPIC_API_KEY=sk-... .venv/bin/lusterna run \
    tests/fibonacci tests/fibonacci/DESIGN.md
```

The `Dockerfile` (as opposed to `Dockerfile.test`) installs the real toolchain: Rust via `rustup`, [Charon](https://github.com/AeneasVerif/charon) from its nightly release, Aeneas built from source with Lake, and Lean 4 via `elan`. Building it takes approximately 30 minutes and several gigabytes of disk space.
