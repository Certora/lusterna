# The Claude-Code Discipline — Lusterna's architecture

Status: **IMPLEMENTED + validated** (full fib pipeline, spine-only, all-CC). Branch:
`claude-code-discipline`. The engine is the **headless `claude` CLI** invoked over `docker exec`
(Claude Code v2.1.218) — the Python `claude-agent-sdk` was evaluated (§10 of the knob inventory)
and set aside: the toolchain image has no Python, so the CLI over `docker exec` is the clean fit
and needs nothing extra in-process. Any "SDK" wording below is historical from the design phase.

## 1. Why

An unbudgeted metavault run crashed in EXPLORE after ~2.8M tokens. The agent rabbit-holed —
patching vendored trust-boundary crates to force a whole-crate build, *against an explicit `STOP`
in its own prompt* — then degenerated emitting a 36K-char structured `ExploreResult`
(`…Now.]}Now.]}`), which failed JSON validation twice and crashed the run uncaught.

Root cause was **not** a missing instruction. It was a **missing mechanism**. Our stage agents
*internalize* everything and *emit it once* at the end as a structured blob; Claude Code
*externalizes incrementally* — every file, note, and todo persists the moment it happens. That one
difference produced all three failure modes: it **wandered** past the STOP (no decision ever
crystallized), **lost everything** on failure (nothing on disk to checkpoint), and **degenerated**
on the terminal monolith (a large forced output after a long trajectory is itself the collapse
trigger).

## 2. Thesis — trust vs labor

Do not *mimic* Claude Code in our own agent framework. **Spawn Claude Code as the engine** for each
stage, and split the pipeline along one line:

- **Claude Code owns 100% of the labor** — reading code, driving charon/aeneas/lake, writing Lean,
  attempting proofs, judging, reporting. It brings todos, incremental files, context compaction,
  sub-agents, resume, and cost caps *for free*.
- **The spine owns 100% of the trust** — the gates, their harness-enforced isolation, the audit
  trail, and the reproducible stage sequence. This is *precisely the code the AI is not allowed to
  write or run*. It cannot live inside the session it judges.

A verifier's one irreducible job is to turn an *untrusted* artifact-producer into a *trusted*
verdict. If the same session writes a proof, runs `#print axioms`, and interprets the result, there
is no verification — only a confident LLM. The spine exists to be the thing the agent cannot fool.

## 3. Architecture (spawn model)

```
run_session (spine, deterministic)
  for stage in [EXPLORE, INFER, TRANSLATE, (TRANSLATE-JUDGE), FORMALISE, (SPEC-JUDGE), PROVE, REPORT]:
      spawn a Claude Code session in the container with:
          - a task briefing (goal + the artifact path it must produce)
          - the skill (playbook) + project CLAUDE.md (invariants)
          - permission_mode = autonomous, cost/turn cap, trust-protecting hooks
      → the session drives the toolchain, keeps a plan + notes, writes its deliverable to /workspace/out, commits
      harness then applies the MECHANICAL GATES to the committed files:
          - lake build (compile), #print axioms (soundness), stub_proofs (no-smuggle), repo diff (audit)
      → gate satisfied: advance.  not satisfied: re-invoke the stage session with the gate output (bounded).
      → checkpoint {stage, cc_session_id, out_git_head}
```

The stages are still EXPLORE → INFER → TRANSLATE → TRANSLATE-JUDGE → FORMALISE → SPEC-JUDGE →
PROVE → REPORT. What changes is that each is a Claude Code session, not a pydantic-ai agent, and the
Python between them is only sequencing + gates + checkpoint.

## 4. Locked decisions

- **One Claude Code session per stage.** Bounds each context (the compaction defense — the metavault
  collapse was one session ballooning), makes the per-stage cost cap meaningful, and keeps the
  judge's context independent of the author's.
- **Hybrid resume.** Prefer re-attaching the kept-alive container's CC session via `resume=<id>`;
  fall back to a deterministic **re-run of the stage from the last committed artifact** when the
  container is gone. Leans on CC's own session machinery; never duplicates it.

## 5. The trusted spine — stays in Python (mechanical, agent-inaccessible)

| Component | Why it's trust |
|---|---|
| `check_axioms` (`#print axioms` vs `_STD_AXIOMS`) | The proof-soundness verdict. Never delegable. |
| `build` / `translation_compiles` (`lake build`) | The "does it actually typecheck" anchor. |
| `_write_lakefile` / `setup_lake` | Makes the compile gate *meaningful* (a bad lakefile silently builds nothing and passes). Harness-owned; never agent-written. |
| `stub_proofs` | Forces FORMALISE bodies to `sorry` before acceptance → only PROVE earns a proof. |
| `referenced_defs` (+ `_def_blocks`, `_mentions`) | Partitions established theorems into verifies-the-code vs abstract-lemma → integrity of the headline claim. |
| `repo_diff` / `_repo_baseline` + `init_repo_git` / `refold_baseline` | Pristine-baseline diff = the audit trail of every source edit. |
| stage **sequence + gate placement** (`run_session`) | The reproducible staged process. |
| *(thin)* `matched_target_defs` / `opaqued_targets` / `target_holes` | Cheap "target not mocked" signal — largely subsumed by `check_axioms`. Keep, not load-bearing. |

~300–400 lines of genuinely trusted code. Small on purpose — it's the entire value.

## 6. Labor → Claude Code

The six stage agents and both judges become CC sessions. The stage **prompts** (`stages.py`) and the
`docs/` **playbooks survive as the skill + task briefings** — the accumulated domain expertise is the
crown jewel and does not evaporate.

## 7. Plumbing — substrate, stays (neither trust nor labor)

`container.py` (lifecycle, `exec_in`, image — gains the CC engine), `checkpoint.py` (thins to
`{stage, cc_session_id, out_git_head}`), `cli.py`, `config.py`, `telemetry.py` (usage now largely
from CC's `ResultMessage`), `tools.read_out/write_out/commit` (harness IO helpers).

## 8. Evaporates — because CC already has it

- **`schemas.py`** — every structured schema. Gone.
- **`factory.py`** — `_TransientRetry`, `AnthropicCompaction` wiring, the token-budget hook. CC does
  retry, compaction, and cost caps natively.
- **`runner.py`** — `_run_stage` (pydantic-ai iteration), `_pipeline_briefing`, the bash-tool wiring.
  Replaced by "launch a CC session pointed at the artifact files."
- **`pipeline.py` loop machinery** — `_progress_score`, stall tracking, `attribute_errors`,
  quarantine, `_handle_build_failure`, `_build_feedback`, best-restore-by-score. A CC session reads
  its own build errors and iterates; the residual loop is *run gate → re-invoke with output →
  bounded retries*.
- **`lean.py` analysis** — `analyze_translation`, `assemble_impl_spec` (FORMALISE writes `Spec.lean`
  directly), `translation_index`, `_detect_holes`, `attribute_errors`.

## 9. Knob / configuration map

| Concern | Mechanism | Setting |
|---|---|---|
| Autonomy | permission mode + allowlist | `--permission-mode dontAsk --allowedTools "Bash,Edit,Write,Read,Glob,Grep"`. NOT `bypassPermissions`: it maps to `--dangerously-skip-permissions`, which CC **refuses to run as root** (our container is root). `dontAsk`+allowlist is autonomous, no prompts, and arguably safer (explicit allowlist). Spike-confirmed. |
| Per-stage runaway cap | CLI `--max-budget-usd` (confirmed v2.1.218) | a hard dollar cap per stage session — **the within-round backstop we lacked**, native |
| Planning | not automatic — must prompt | task briefing / CLAUDE.md *requires* a plan for non-trivial stages, then "write conclusions to `notes.md` as reached, stop when the deliverable exists" |
| Notes / working memory | files | per-stage `<stage>/notes.md` under `/workspace/out` (git-tracked, survives crash & resume) |
| Invariants | project memory | `CLAUDE.md` in `/workspace`: the soundness ladder, "don't grind out-of-scope deps", the trust boundary |
| Playbook | skill | `.claude/skills/aeneas/SKILL.md` (the translatability inventory, proof strategies) |
| Compaction | on by default, overflow = hard stop | defense is per-stage session scoping; `PreCompact` hook archives if it fires |
| Config delivery | ship in container | `.claude/settings.json` under `/workspace`; opt in via CLI `--settings` / `--setting-sources` |
| Cost/usage accounting | `ResultMessage.total_cost_usd` / `.usage` | harness sums per stage into the session total |

## 10. Soundness invariants (inviolable — hold across the whole rewrite)

1. `#print axioms` and the `lake build` gate are **never** weakened or delegated to a judge or to
   agent self-report; the harness's post-hoc run is the only one that counts.
2. The harness **rebuilds `Spec.lean` from committed source with its own freshly-generated
   lakefile before `check_axioms`** — never trusts an agent-produced `.olean` or lakefile. (With CC
   holding full bash in the container, this is what keeps the gate un-foolable in-session.)
3. `stub_proofs` runs on the FORMALISE artifact before it is accepted.
4. Every source edit is captured in the pristine-baseline diff for the audit trail.

## 11. Trust integration via hooks (config becomes enforcement)

- **`PreToolUse`** matcher on Write/Edit/Bash → **deny writes to the trusted files** (the
  harness-generated lakefile, the pristine baseline, the axiom-checker scratch). Complements §10.2.
- **`Stop` / `SubagentStop`** → assert the stage's deliverable file actually exists before the stage
  is allowed to end (kills the "stopped with nothing on disk" failure).
- **`PreCompact`** → archive the transcript so a compaction never silently drops audit context.

## 12. Container & image changes — RESOLVED (headless CLI in-container, host-driven)

Recon settled the shape. The host already has `claude` 2.1.218 + node; the toolchain image has
**none of node/claude/python3** — because the harness runs on the host and drives the container via
`docker exec`. We keep that split:

- **The harness stays on the host** (Python, as now). Instead of pydantic-ai, a stage is launched by
  `exec_in(container, ["claude", "-p", …])` — Claude Code runs **headless inside the container**, so
  its own Bash/Read/Write/Edit operate directly on `/workspace` where the toolchain and artifacts
  live. No Python or SDK in the image.
- **Image gains only** `node` + the `claude` CLI (`npm i -g @anthropic-ai/claude-code`).
- **Auth**: `ANTHROPIC_API_KEY` passed into the container env (never in a checked-in settings file).
- Confirmed CLI surface (v2.1.218) — the whole engine is flag/config-file driven:
  - `-p --output-format json` → one parseable result (session_id, result, cost) from stdout.
  - `--session-id <uuid>` → **we mint the id at spawn** (no output-parsing race); `-r/--resume`,
    `--fork-session` for the hybrid-resume path.
  - `--max-budget-usd <amount>` → the per-stage runaway cap.
  - `--permission-mode dontAsk --allowedTools "Bash,Edit,Write,Read,Glob,Grep"` → full autonomy.
    (`bypassPermissions`/`--dangerously-skip-permissions` is refused as root — the container is root.)
  - `--settings <file>` / `--setting-sources` → ship `.claude/settings.json` (hooks, permissions);
    `--append-system-prompt` → the task briefing; `--agents <json>` → judges if wanted; `--effort`,
    `--model`, `--add-dir`, `--mcp-config`.
- The agent's old single `bash` tool goes away — CC has its own. `exec_in` remains for the harness's
  own gate runs.

**Stage invocation (shape):**
`docker exec -e ANTHROPIC_API_KEY <c> claude -p --session-id <uuid> --output-format json \
  --permission-mode dontAsk --allowedTools "Bash,Edit,Write,Read,Glob,Grep" --max-budget-usd <cap> \
  --settings /workspace/.claude/settings.json --append-system-prompt "<task briefing>" "<stage prompt>"`
— run with cwd `/workspace`; harness parses stdout for `{session_id, total_cost_usd, result}`, then gates.

## 13. Verify — RESOLVED by recon (host `claude` 2.1.218)

- ✓ CLI surface (§12) confirmed: `-p`, `--output-format json`, `--session-id`, `--resume`,
  `--fork-session`, `--max-budget-usd`, `--permission-mode {…,bypassPermissions}`, `--settings`,
  `--setting-sources`, `--append-system-prompt`, `--agents`, `--effort`, `--model`.
- ✓ No SDK/Python in the image — headless CLI + `docker exec` (image adds `node` + `claude` only).
- ✓ `session_id` needs no capture race — we mint it with `--session-id <uuid>`.

✓✓ **Spike done (throwaway container, image with node+claude, real `--cap-drop all` profile):**
- ✓ `--output-format json` returns `{session_id, total_cost_usd, result, usage, num_turns,
  permission_denials, stop_reason, …}` — everything the harness needs.
- ✓ CC drives bash in-container and writes `/workspace/out` (its Read/Write/Bash operate there).
- ✓ `--session-id <uuid>` sets identity; `--resume <uuid>` carries full context (recalled a filename
  from the prior turn unprompted).
- ✓ A `--settings` `PreToolUse` hook **fires headless** → §11 trust hooks are real.
- ✓ Session store: `/root/.claude/projects/-workspace/<uuid>.jsonl` (cwd-keyed) — `keep_alive`
  preserves it, so hybrid re-attach works.
- ✓ Permission model as root: `dontAsk` + `--allowedTools` (see §9).

## 14. Migration plan (fib green after every phase)

1. **Spike** — one throwaway stage (EXPLORE) spawned via the headless CLI inside the container end-to-end,
   verifying §13.1–4. Nothing else changes.
2. **Spine skeleton** — `run_session` sequences CC-session stages; the mechanical gates stay exactly
   as they are; checkpoint thins to `{stage, cc_session_id, out_git_head}`.
3. **EXPLORE** (the crash fix) → **INFER** → **FORMALISE** (harness `stub_proofs` + compile gate
   stay) → **TRANSLATE** → **judges** → **REPORT**, one stage at a time.
4. **Retire** the evaporated modules (§8) once no stage depends on them.

Soundness anchors (§10) hold throughout.
