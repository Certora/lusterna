# Lusterna

An AI agent that translates Rust programs into formally verified Lean 4 specifications.

Given a Rust repository and a design document, Lusterna:

1. Explores the source and queries a local knowledge base for domain context
2. Translates the Rust code to Lean 4 via [Aeneas](https://github.com/AeneasVerif/aeneas)
3. Infers an informal specification from the translated code and the design document
4. Derives a formal Lean 4 specification (theorem stubs)
5. Has a judge subagent evaluate and score the spec; revises if the score is below 7
6. Writes a final verification report

All generated artefacts are git-committed incrementally inside the toolchain container and pulled to the host on exit.

## Architecture

```
lusterna/
├── cli.py          — Click entry point; manages the container lifecycle
├── agent.py        — Orchestrator agent (pydantic-ai); owns the pipeline loop
├── subagents.py    — Specialist subagents: spec inferrer, formaliser, judge, summariser
├── tools.py        — All agent-callable tools (file I/O, Aeneas, Lake, RAG, git)
├── container.py    — Docker lifecycle: start, push repo, exec, pull artefacts, stop
├── git_ops.py      — Git commands run inside the container via docker exec
├── state.py        — AgentDeps: typed dependency bundle injected into every tool
├── checkpoint.py   — Atomic JSON checkpoints on the host filesystem
├── compaction.py   — Context summarisation when the token estimate exceeds the threshold
├── rag.py          — File-based RAG: JSONL documents + numpy embedding index
├── config.py       — All knobs via environment variables
└── logging_setup.py — stdlib logging → stderr; level from LUSTERNA_LOG_LEVEL
```

### Pipeline

```
CLI
 └─ start container
     └─ docker cp repo → /workspace/repo
     └─ git init /workspace/out
         └─ Orchestrator agent (pydantic-ai)
             ├─ EXPLORE   list_files, read_file, rag_query
             ├─ TRANSLATE run_aeneas        → lean/          (git commit)
             ├─ INFER     infer_spec        → specs/         (git commit)
             │             └─ spec-inferrer subagent
             ├─ FORMALISE formalise_spec    → specs/         (git commit)
             │             └─ formal-spec subagent
             ├─ JUDGE     judge_spec                         (git commit)
             │             └─ judge subagent  (re-formalise if score < 7)
             └─ REPORT    write_file        → VERIFICATION_REPORT.md
 └─ docker cp /workspace/out → host out_dir
 └─ stop container
```

### Docker interaction

The toolchain (Rust/Cargo, Charon, Aeneas, Lean/Lake) lives entirely inside a Docker container. There are no bind-mounts: the source repo is pushed in with `docker cp` at session start and artefacts are pulled back out at the end. This means:

- The container has its own isolated filesystem — no host paths are exposed
- The agent cannot reach anything outside `/workspace/repo` (read-only by convention) or `/workspace/out` (writable)
- The container runs with `--network none`, `--cap-drop all`, and `--security-opt no-new-privileges`

All toolchain invocations go through `docker exec`. Git also runs inside the container so the commit history is part of the pulled artefacts.

### Subagents

| Agent | Output type | Purpose |
|---|---|---|
| `_spec_inferrer` | `InformalSpec` | Reads Lean + design doc → preconditions, postconditions, invariants, edge cases |
| `_formal_spec_writer` | `FormalSpec` | Turns informal spec → Lean 4 definitions and theorem stubs |
| `_judge` | `JudgeVerdict` | Scores the formal spec 0–10; lists gaps and suggestions |
| `_summariser` | `str` | Condenses message history when the context threshold is reached |

### Context management

Token usage is estimated with a character-based heuristic (4 chars ≈ 1 token). When the estimate exceeds `LUSTERNA_COMPACTION_THRESHOLD` (default 80 000 tokens), the summariser subagent condenses all but the last two messages into a single system prompt entry, keeping the orchestrator coherent across long sessions.

### Checkpoints

After every pipeline stage that mutates state, `checkpoint.save()` writes an atomic JSON file to `~/.local/share/lusterna/checkpoints/<session-id>.json`. A session can be resumed with `--session-id`; the CLI reattaches to a running container if the checkpoint records one, or starts a fresh container otherwise.

### RAG knowledge base

`rag.py` maintains a flat file store under `~/.local/share/lusterna/rag/`:

```
docs.jsonl        — one JSON object per line: {id, text, source, tags[]}
embeddings.npy    — float32 matrix (N, D), row i = embedding of docs[i]
```

Similarity search uses cosine distance over numpy vectors — no external vector database needed. Ingest documents with `lusterna rag add`.

## Requirements

- Python 3.10+
- Docker (with access to the Docker daemon)
- An Anthropic API key (`ANTHROPIC_API_KEY`)

## Installation

```sh
pip install -e .
```

## Quickstart

```sh
# 1. Build the toolchain image (one-time, ~5 min for the real image)
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
lusterna [--verbose] [--no-confirm] COMMAND
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
| `--container NAME` | (auto-start) | Attach to a pre-running toolchain container |
| `--image TAG` | `lusterna-toolchain:latest` | Image to start when `--container` is not given |

Output (stdout, JSON):

```json
{
  "session_id": "...",
  "out_dir": "/path/to/rust-repo-lusterna",
  "container_id": "...",
  "summary": "...",
  "progress_keys": ["aeneas", "informal_spec", "formal_spec", "verdict"]
}
```

Progress and errors go to stderr via structured log lines.

### `build-image`

```
lusterna build-image [--tag TAG]
```

Build the `lusterna-toolchain` Docker image from the project `Dockerfile`. Required before the first `run`. Uses `Dockerfile.test` during development (stub `aeneas`/`lake`).

### `list-sessions`

```
lusterna list-sessions
```

Print all saved session IDs (one per line).

### `show-session`

```
lusterna show-session SESSION_ID
```

Print the checkpoint JSON for `SESSION_ID` to stdout.

### `rag add`

```
lusterna rag add FILE [FILE ...] [--source SOURCE] [--tags tag1,tag2]
```

Ingest one or more documents into the local RAG knowledge base. Requires a Voyage AI client (`pip install voyageai`) for embedding.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Anthropic API key |
| `LUSTERNA_MODEL` | `anthropic:claude-sonnet-4-6` | Model for the orchestrator and most subagents |
| `LUSTERNA_JUDGE_MODEL` | `anthropic:claude-sonnet-4-6` | Model for the judge subagent |
| `LUSTERNA_COMPACTION_THRESHOLD` | `80000` | Estimated token count that triggers context compaction |
| `LUSTERNA_RAG_DB` | `~/.local/share/lusterna/rag` | Path to the RAG knowledge base |
| `LUSTERNA_CHECKPOINT_DIR` | `~/.local/share/lusterna/checkpoints` | Path for session checkpoints |
| `LUSTERNA_AENEAS_BIN` | `aeneas` | Aeneas binary name inside the container |
| `LUSTERNA_LAKE_BIN` | `lake` | Lake binary name inside the container |
| `LUSTERNA_IMAGE` | `lusterna-toolchain:latest` | Default Docker image |
| `LUSTERNA_CONTAINER` | — | Pre-existing container to attach to (skips auto-start) |
| `LUSTERNA_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Resuming a session

Each pipeline stage saves a checkpoint. If a run is interrupted:

```sh
# find the session ID
lusterna list-sessions

# resume — a fresh container is started and the agent picks up where it left off
lusterna run /path/to/repo design.md --session-id <uuid>
```

## Output artefacts

After a run, `<out_dir>/` contains a git repository with one commit per pipeline stage:

```
<out_dir>/
├── explore_notes.md          — EXPLORE stage notes
├── lean/
│   ├── main.lean             — Aeneas translation
│   └── <Project>/Spec.lean   — Formal specification (theorem stubs)
├── specs/
│   ├── informal_spec.json    — Structured informal specification
│   └── formal_spec.lean      — Raw formal spec from the formaliser subagent
└── VERIFICATION_REPORT.md    — Final report: theorem status, open obligations, proof sketches
```

```sh
git -C <out_dir> log --oneline
# eb4c22e docs: add VERIFICATION_REPORT.md — final pipeline report
# 9ae3f57 feat: formal verification pipeline for fibonacci
# 9e4be47 feat(spec): formal specification stubs
# ac05db8 feat(spec): informal specification
# 51ab72f feat(aeneas): translate src/main.rs → Lean
# 7bc149d chore: init lusterna session
```

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e . pytest

# Use the fast stub image (aeneas and lake are shell scripts that return immediately)
docker build -f Dockerfile.test -t lusterna-toolchain:latest .

# Run against the included example
ANTHROPIC_API_KEY=sk-... .venv/bin/lusterna --no-confirm run \
    tests/fibonacci tests/fibonacci/DESIGN.md
```

The `Dockerfile` (as opposed to `Dockerfile.test`) installs the real toolchain: Rust via `rustup`, [Charon](https://github.com/AeneasVerif/charon) from its nightly release, Aeneas built from source with Lake, and Lean 4 via `elan`. Building it takes approximately 30 minutes and several gigabytes of disk space.
