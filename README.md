# Lusterna

An AI agent that translates Rust programs into formally verified Lean 4 specifications.

Lusterna **spawns an AI agent as the engine for each pipeline stage** — a headless agent
session, running inside the toolchain container, drives Charon/Aeneas/Lake and does all the file
and proof work itself. The Python harness is a thin, **trusted spine**: it sequences the stages and
runs the soundness gates that the agents are not allowed to touch.

Given a Rust repository and a design document, Lusterna:

1. **Explores** the source (entry file + public functions) and empirically reality-checks the
   toolchain — building with Charon and a coarse Aeneas pass to record what is buildable, the
   external trust boundary, and which types must be modelled — as an advisory assessment
2. **Infers**, from the code, the behavioural properties the target functions satisfy — and which
   functions the verification targets (the design document is only a focus hint; the code is the
   source of truth)
3. **Translates** the target to Lean 4 via [Charon](https://github.com/AeneasVerif/charon) +
   [Aeneas](https://github.com/AeneasVerif/aeneas), driving the toolchain to translate the target's
   own logic (never mocking it away)
4. **Formalises** the properties as Lean 4 theorem statements and builds them with `lake build`,
   iterating until they compile
5. Has a **spec-judge** list concrete defects in the theorem statements (checked against the
   translation and the inferred properties); revises until the list is empty
6. **Proves** as many statements as it can with Lean 4 tactics against the `lake build` oracle
7. Runs **`#print axioms`** — the authoritative gate: a theorem is *established* only if its proof
   depends on nothing beyond the standard axioms (so an untranslated hole, a leftover `sorry`,
   `native_decide`'s compiler trust, or an assumed axiom all leave it reported as *tainted*, not
   verified)
8. Writes a **verification report** — led by a harness-generated verdict block that no agent
   narrative can override.

Each stage is one AI-agent session with its own tools; its **deliverable is files** under
`/workspace/out` (not a structured blob). The harness reads those files and applies the mechanical
gates. The session's activity is streamed to the host log live, so you can watch what it does.

## The spawn model — trust vs labor

The one line the whole design is built on:

- **The AI agent owns the labor** — reading code, driving charon/aeneas/lake, writing Lean,
  attempting proofs, judging, reporting. It brings todos, incremental file-based deliverables,
  context compaction, resume, and per-run cost caps for free.
- **The harness owns the trust** — a small, deterministic, agent-inaccessible spine: the soundness
  gates, their isolation, the audit trail, and the reproducible stage sequence. This is *precisely
  the code the AI is not allowed to write or run.*

The inviolable gates, run by the harness on the files a stage produced (never delegated to an
agent, never inferred from "it compiled"):

- **`#print axioms`** (`lean.check_axioms`) — the authoritative established-vs-tainted verdict;
- **`lake build`** — the compile gate;
- **`stub_proofs`** — re-stubs FORMALISE's theorem bodies to `sorry` before acceptance, so only
  PROVE can earn a proof;
- **the pristine-baseline git diff** — the audit trail of every source edit.

A stage runs as an AI-agent session; the harness then applies that stage's gate and, if it is not
satisfied, **resumes the session with the gate's feedback** until it passes or a progress-aware
stall trips (`STALL_ROUNDS` consecutive rounds with the *same* failure — a genuinely-improving loop
is never cut off). Each session is bounded by the agent's own `--max-budget-usd`.

## Pipeline

```mermaid
flowchart TD
  DESIGN["📄 DESIGN.md<br/>focus hint"]:::src
  RUST["🦀 Rust crate<br/>source of truth"]:::src

  EXPLORE["EXPLORE<br/>entry file + fns + toolchain assessment"]:::impl
  INFER["INFER<br/>behaviour spec + target_patterns<br/>(pristine source)"]:::impl
  TRANSLATE["TRANSLATE<br/>agent session drives Charon → Aeneas → Lean"]:::impl
  TJUDGE{"TRANSLATE-JUDGE<br/>target translated & faithful?"}:::gate
  FORMALISE["FORMALISE<br/>theorem statements"]:::impl
  FBUILD{"stub_proofs + lake build<br/>statements compile?"}:::gate
  SJUDGE{"SPEC-JUDGE<br/>defects?"}:::gate
  PROVE["PROVE<br/>discharge sorry vs lake build"]:::impl
  AXIOMS["#print axioms<br/>established-theorem gate"]:::verify
  REPORT["REPORT"]:::report

  RUST --> EXPLORE --> INFER --> TRANSLATE --> TJUDGE
  TJUDGE -- "defects (mock / hole / unfaithful)" --> TRANSLATE
  TJUDGE -- "approved" --> FORMALISE --> FBUILD
  FBUILD -- "✗ fix" --> FORMALISE
  FBUILD -- "✓" --> SJUDGE
  SJUDGE -- "defects" --> FORMALISE
  SJUDGE -- "clean" --> PROVE --> AXIOMS --> REPORT
  DESIGN -. "hint" .-> INFER

  classDef src    fill:#e2e8f0,stroke:#475569,stroke-width:1.5px,color:#0f172a;
  classDef impl   fill:#cffafe,stroke:#0891b2,stroke-width:1.5px,color:#083344;
  classDef gate   fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#451a03;
  classDef verify fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#052e16;
  classDef report fill:#e2e8f0,stroke:#334155,stroke-width:1.5px,color:#0f172a;
```

Each stage runs with a fresh AI-agent session (fresh context — the judge's context is
independent of the author's); stages communicate via git-committed files under `/workspace/out`,
not via message history.

| Stage | Deliverable (files) | Harness gate |
|---|---|---|
| EXPLORE | `explore/{assessment.md, handoff.json}` | valid handoff JSON |
| INFER | `specs/informal_spec.json` (properties + `target_patterns`) | valid JSON with `target_patterns` |
| TRANSLATE | `lean/<Crate>.lean` + `translate/{plan,accountability}.md` | compiles · target is a real `def` (not opaqued/holed) · single top-level module |
| TRANSLATE-JUDGE | `translate/verdict.json` | empty defect list (semantic screen; independent session) |
| FORMALISE | `lean/<Crate>/Spec.lean` (statements, bodies `:= by sorry`) | `stub_proofs` + `lake build` compiles |
| SPEC-JUDGE | `spec/verdict.json` | empty defect list |
| PROVE | proofs filled into `Spec.lean` | `#print axioms` (best-tracked: maximise established count) |
| REPORT | `report/NN_*.md` | — (harness prepends the authoritative verdict) |

FORMALISE cannot smuggle in a proof: the harness runs `stub_proofs` on its `Spec.lean` before the
compile gate, so every theorem body is `sorry` and only PROVE fills them. The `#print axioms` gate
after PROVE is the authoritative verdict; the report's headline metric — generated by the harness,
not the agent — is the number of established theorems that also reference an Aeneas-translated def.

## TRANSLATE

TRANSLATE is where the target Rust becomes Lean. The session drives Charon and Aeneas at the shell,
scoping to the target's functions (`--start-from`) and getting the target's own logic translated as
real Lean `def`s. Aeneas has a limited Rust fragment, so on larger targets some dependencies do not
translate. The session handles each by the least-degrading option that works:

- **scope** — `--start-from` the target's call-closure and drop out-of-closure noise;
- **assume** — `--opaque` a trusted leaf dependency the properties do not reason about (crypto,
  hashing, transcripts, formatting); Aeneas emits it as a Lean `axiom`, which the `#print axioms`
  gate then flags on any theorem that depends on it;
- **model** — a behaviour-preserving source refactor when a data structure the properties *do*
  depend on has no Lean model (e.g. a `BTreeMap` ledger → an association list); confirmed against the
  crate's own `cargo test` and recorded, with the diff, in `translate/accountability.md`.

A **TRANSLATE-JUDGE** (a separate, independent session) gates the result: it rejects a translation
that mocks the target (opaques a target function, or opaques a data structure whose contents a
property constrains) or that changes observable behaviour. Every alteration is disclosed for human
review; if the target cannot be translated without mocking it, the run aborts rather than emitting a
hollow translation.

To make the session recognise-and-apply rather than rediscover Aeneas's fragment every run, the
translatability playbook (`docs/skills/aeneas-translate.md`) is appended to the TRANSLATE briefing —
a verdict table (what translates / opaques / holes / rejects), the modelable-stdlib line,
behaviour-preserving recipes, and the charon/aeneas mechanics.

### The translatability inventory

The playbook is **measured**, not folklore. Aeneas's translatable fragment is defined by the
toolchain, so `tools/aeneas-characterize/` measures it directly rather than relying on anecdote:

- **What it does.** `characterize.py` runs a corpus of tiny single-construct probe crates (a
  `BTreeMap`, an iterator chain, an `Option` combinator, a closure, a trait object, …) through
  charon+aeneas once, in the toolchain image, and records the verdict for each — `def` (translates)
  / `axiom` (opaqued, no Lean model) / `hole` (`sorry`) / `error` (rejected). It also extracts
  Aeneas's builtin registry (`extract/ExtractBuiltin*.ml`) — the authoritative "what stdlib has a
  Lean model" set that decides def-vs-axiom. Output: `characterization.json`.
- **How to run it.** `python tools/aeneas-characterize/characterize.py` (needs the toolchain image
  and the editable-installed package). It prints a table and writes the JSON.
- **When.** On a toolchain-image bump — the fragment is version-specific. Then reconcile the
  playbook's verdict table and recipes with the fresh `characterization.json`.

## Architecture

```
lusterna/
├── cli.py          — Click entry point; manages the container lifecycle
├── pipeline.py     — The spine: per-stage functions that spawn an AI-agent session and apply
│                     the trusted gate (the _cc_gate_loop / PROVE best-loop), + run_session
├── runner.py       — run_cc_stage: launch `claude -p` headless in-container and stream its
│                     stream-json activity to the host log; mint/resume the session id
├── briefings.py    — The task briefing per stage (the spawn-model prompt library)
├── lean.py         — Aeneas/Lean domain logic: translation analysis, lake build, the
│                     #print axioms gate (check_axioms), stub_proofs, spec operations
├── tools.py        — The harness's own file/git IO helpers inside the container (not agent tools)
├── schemas.py      — AgentDeps (the shared dependency handle)
├── docs.py         — Aeneas/Lean skill documents appended to the relevant briefings
├── container.py    — Docker lifecycle: start, push repo, exec, exec_stream, reset/pull artefacts
├── checkpoint.py   — Per-session numbered checkpoints + snapshot(deps) serialisation
└── config.py       — Env-driven knobs, plus logging setup

tools/aeneas-characterize/   — build-time harness that measures Aeneas's translatable
                               fragment (see above); seeds the TRANSLATE playbook.
```

There is no in-process LLM SDK: the engine is the `claude` CLI (installed in the toolchain image),
invoked over `docker exec`. `pydantic-ai` was retired with the spawn-model migration.

## Docker interaction

The toolchain (Rust/Cargo, Charon, Aeneas, Lean/Lake) **and the AI agent itself** (Node + the
agent CLI) live entirely inside a Docker container. There are no bind-mounts: the source repo is
pushed in via a tar pipe at session start and artefacts are pulled back out at the end.

- The container has its own isolated filesystem — no host paths are exposed. The source is at
  `/workspace/repo` (git-initialised at a pristine baseline so any behaviour-preserving edit is
  captured as a diff); generated artefacts go to `/workspace/out`.
- Each stage runs as `docker exec … claude -p …` inside the container, with `ANTHROPIC_API_KEY`
  forwarded (never written to a file). Autonomy is `--permission-mode dontAsk` + an explicit tool
  allowlist (`bypassPermissions` is refused as root), and `--max-budget-usd` caps each session.
- The container runs with `--cap-drop all` and `--security-opt no-new-privileges`. Network is
  enabled so Charon can `cargo build` targets whose dependencies are fetched on demand, and so
  the agent can reach the Anthropic API.

## Checkpoints & resuming

After each stage the harness saves a numbered checkpoint under a per-session directory:

```
~/.local/share/lusterna/sessions/<session-id>/checkpoint-001.json …
```

Each records the progress dict (incl. per-stage agent session ids and costs), container ID,
repo/work paths, design doc, and the `git_head` SHA of `/workspace/out` at save time.

An interrupted run (a stage session failing unrecoverably, or a graceful abort) is stopped
gracefully — never a traceback — and its container is **kept alive**, so a resume re-attaches to it
with full in-stage state and continues rather than restarting the stage. Only if that container is
gone does resume rebuild a fresh one: it restores the artefacts from `--out` and hard-resets the
output repo to the checkpoint's `git_head`, continuing from exactly that state.

```sh
lusterna list-sessions
lusterna list-checkpoints <session-id>

# Resume from the latest checkpoint (or a specific one with --checkpoint-number N).
lusterna run /path/to/repo design.md --session-id <uuid> --out /path/to/out
```

## Requirements

- Python 3.10+ (only `click`; no LLM SDK)
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

# Artefacts land in /path/to/rust-repo-lusterna/ by default; override with --out.
```

## Commands

```
lusterna run REPO DESIGN_DOC [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--out DIR` | `<repo>-lusterna/` | Host directory to pull artefacts into |
| `--session-id ID` | (new UUID) | Resume a previous session |
| `--checkpoint-number N` | (latest) | Checkpoint to resume from within a session |
| `--container NAME` | (auto-start) | Attach to a pre-running toolchain container |
| `--image TAG` | `lusterna-toolchain:latest` | Image to start when `--container` is not given |

Output is JSON on stdout (`session_id`, `out_dir`, `container_id`, `summary`, `progress_keys`);
the live per-stage activity trail and progress go to stderr as structured log lines.

```
lusterna build-image [--tag TAG]      # build the toolchain image (required before the first run)
lusterna list-sessions                # sessions that have at least one checkpoint
lusterna list-checkpoints SESSION_ID  # checkpoints for a session (JSON)
lusterna show-checkpoint SESSION_ID [--number N]   # a checkpoint's state (JSON)
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Anthropic API key, forwarded into the container |
| `LUSTERNA_CC_MODEL` | `opus` | The `claude --model` alias for the spawned stage sessions |
| `LUSTERNA_EFFORT` | `high` | Reasoning effort (`--effort`): "", low, medium, high, xhigh, max |
| `LUSTERNA_CC_STAGE_BUDGET_USD` | `50` | Per-stage `--max-budget-usd` cap — a runaway backstop, not a work limiter |
| `LUSTERNA_STALL_ROUNDS` | `3` | Consecutive **same-failure** rounds before a stage's gate loop gives up (no hard round ceiling; a progressing loop continues) |
| `LUSTERNA_BUILD_TIMEOUT` | `180` | Per-`lake` timeout (seconds) |
| `LUSTERNA_SESSIONS_DIR` | `~/.local/share/lusterna/sessions` | Root for per-session checkpoints |
| `LUSTERNA_IMAGE` | `lusterna-toolchain:latest` | Default Docker image |
| `LUSTERNA_CONTAINER` | — | Pre-existing container to attach to (skips auto-start) |
| `LUSTERNA_STOP_AFTER_EXPLORE` | (off) | Stop after EXPLORE so its assessment can be inspected |
| `LUSTERNA_STOP_AFTER_TRANSLATE` | (off) | Stop after TRANSLATE so the translation can be inspected |
| `LUSTERNA_STOP_BEFORE_PROVE` | (off) | Stop after SPEC-JUDGE so the inferred spec can be inspected |
| `LUSTERNA_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Model retry, context compaction, and cost caps are owned by the AI agent itself, so there are no
knobs for them here.

## Output artefacts

After a run, `<out_dir>/` is a git repository with one commit per stage (the multi-GB `.lake` build
tree is excluded):

```
<out_dir>/
├── explore/
│   ├── assessment.md         — EXPLORE's narrative
│   └── handoff.json          — entry file/functions + the toolchain assessment
├── lean/
│   ├── <Crate>.lean          — Aeneas translation (root module)
│   ├── <Crate>/…             — translation submodules + <Crate>/Spec.lean (the theorem spec)
│   └── lakefile.lean         — Lake project file
├── specs/informal_spec.json  — the inferred behavioural properties + target_patterns
├── translate/                — plan.md, accountability.md, verdict.json (every scope/opaque/edit)
├── spec/verdict.json         — the spec-judge verdict
├── report/                   — facts.json + the individual report sections
└── VERIFICATION_REPORT.md    — led by the harness's authoritative #print axioms verdict, then
                                 theorem status, assumptions, and proof sketches
```
