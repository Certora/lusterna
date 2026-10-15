# Usage

## Commands

```
lusterna run REPO DESIGN_DOC [BRANCH] [OPTIONS]
```

`REPO` must be a **full, non-shallow git repository with at least one commit** — the run seeds from a
commit and delivers its results back as a branch, so a bare directory or a shallow clone is refused up
front (run `git init && git add -A && git commit`, or `git fetch --unshallow`, first).

`BRANCH` (optional) is the commit the run **seeds from** and anchors `<branch>-base` at — its tree is
the starting point. Omit it to seed from the target's current `HEAD`. Pass a prior run's
`lusterna/<sid>` branch to make the run **incremental**: the earlier translation/spec/proofs are
reused and only the delta is recomputed (see [Incremental runs](#incremental-runs)).

| Option | Default | Description |
|---|---|---|
| `--session-id ID` | (new UUID) | Session ID. Resumes if a checkpoint by that ID exists; otherwise starts a **new** run under that name (a stable, readable id instead of the default random one). A name that collides with an existing checkpoint resumes it, so choose a fresh one for a new run. |
| `--checkpoint-number N` | (latest) | Checkpoint to resume from within a session |
| `--container NAME` | (auto-start) | Attach to a pre-running toolchain container |
| `--image TAG` | `lusterna-toolchain:latest` | Image to start when `--container` is not given |

The run's results are fetched into `REPO` as branch `lusterna/<session>` (with `lusterna/<session>-base`
for diffing); `REPO` is git-initialised if it is not already a repo, and its working tree and any
existing branches are left untouched. Output is JSON on stdout (`session_id`, `repo`, `branch`,
`container_id`, `summary`, `progress_keys`); the live per-stage activity trail and progress go to
stderr as structured log lines.

```
lusterna build-image [--tag TAG] [--aeneas-ref REF]   # build the toolchain image (required before the first run); REF is an Aeneas branch, tag or commit (default: latest release)
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
| `LUSTERNA_STOP_AFTER_INFER` | (off) | Stop after INFER so the inferred spec + target scope can be inspected |
| `LUSTERNA_STOP_AFTER_TRANSLATE` | (off) | Stop after TRANSLATE so the translation can be inspected |
| `LUSTERNA_STOP_BEFORE_PROVE` | (off) | Stop after SPEC-JUDGE so the inferred spec can be inspected |
| `LUSTERNA_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Checkpoints & resuming

After each stage the harness saves a numbered checkpoint under a per-session directory:

```
~/.local/share/lusterna/sessions/<session-id>/checkpoint-001.json …
```

Each records the progress dict (incl. per-stage agent session ids and costs), container ID,
repo path, design doc, and the `git_head` SHA of the run branch at save time.

An interrupted run (a stage session failing unrecoverably, or a graceful abort) is stopped
gracefully — never a traceback — and its container is **kept alive**, so a resume re-attaches to it
with full in-stage state and continues rather than restarting the stage. Only if that container is
gone does resume rebuild a fresh one: it restores the repo (source edits, `verification/`, and the
branch) from the run branch already fetched into the target repo, and hard-resets to the
checkpoint's `git_head`, continuing from exactly that state.

When resume must start a **fresh agent session** (its predecessor's in-container conversation is gone
with the dead container), the work carries across on **disk, not in the agent's memory**: the stage
briefing tells every resumed session to first re-read the committed artefacts — the translation, spec
modules, established lemmas, and its own `prove/` notes (`assumptions.md`, `refutations.json`) — and
continue from them rather than re-derive. Artefacts are streamed to disk *as* a stage works, not only
at its end, so a resumed session that skipped them would be "unprimed" and re-tread banked work.

```sh
lusterna list-sessions
lusterna list-checkpoints <session-id>

# Resume from the latest checkpoint (or a specific one with --checkpoint-number N).
lusterna run /path/to/repo design.md --session-id <uuid>
```

## Incremental runs

Distinct from resuming: an incremental run is a **new session seeded from a prior run's branch**, so
a fresh campaign builds on earlier work instead of starting over. You pass the prior run's branch as
the seed:

```sh
lusterna run /path/to/repo new-campaign.md lusterna/<prior-session>
```

The prior branch's tree (translation, spec, proofs, source edits) becomes the starting point and the
`-base` anchor, so `git diff lusterna/<new>-base lusterna/<new>` is exactly what the new campaign
added. **What to reuse vs. redo is driven by the instruction document, not by flags** — every stage
is told a prior artefact may already be present and reconciles it against the new instruction
(reuse / extend / revise). Two motivating cases:

- **Grow a campaign** — a new instruction that adds properties: the TRANSLATION is reused, INFER
  adds the new properties, FORMALISE/PROVE handle the delta, and prior proofs carry over.
- **Close remaining `sorry`s** — an instruction to finish the open obligations: everything upstream
  is reused and PROVE re-attacks just the unproven theorems (with more budget/effort).

Reuse saves **labor**, never **trust**: the branch carries Lean *source text* (defs, statements,
proof scripts) but not the compiled `.lake`, so every run rebuilds and re-runs `#print axioms` over
the whole final state — a reused proof is re-verified from scratch, not inherited on faith.

## Output artefacts

After a run, the target repo carries branch `lusterna/<session>` with one commit per stage (the
multi-GB `.lake` build tree is excluded). The branch holds the original source (plus any
behaviour-preserving edit TRANSLATE made) and all generated artefacts under `verification/`:

The mental model: `lean/` is the verified artifact; every other dir is one stage's trail.

```
<repo>/  (on branch lusterna/<session>)
├── …                             — the original source, plus any TRANSLATE source edit
└── verification/
    ├── lean/                      — the verified Lean project (unchanged layout — lakefile-driven)
    │   ├── <Crate>.lean           — Aeneas translation (root module)
    │   ├── <Crate>/…              — translation submodules + <Crate>/Spec.lean (the theorem spec)
    │   └── lakefile.lean          — Lake project file
    ├── infer/campaigns/<Campaign>.json — entry_file + properties/invariants + target_patterns + relevant_state
    ├── translate/                 — plan.md, accountability.md, source.diff, facts.json, verdict.json
    ├── spec-judge/verdict.json    — the spec-judge verdict
    ├── report/                    — axioms.json (authoritative verdicts) + the report sections
    └── VERIFICATION_REPORT.md     — led by the harness's authoritative #print axioms verdict, then
                                      theorem status, assumptions, and proof sketches
```

Review the whole run as a single diff:

```sh
git -C <repo> diff lusterna/<session>-base lusterna/<session>
```
