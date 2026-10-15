# Design choices

The harness is about 2,500 lines of Python and Lean with no real dependencies, no LLM SDK, no
agent framework, no orchestration or graph library, no vector store, no database. It contains the
trusted spine described above, i.e. the soundness gates, their container isolation, the audit trail, and
the stage sequence, and nothing else. All labor runs in a standalone coding agent spawned in the
container at each stage; the harness reimplements none of its scaffolding (planning, file IO,
retries, context management, session resume).

This is *deliberate*, because that spine is where the verification guarantee actually
lives. A language model is capable at the labor and structurally unreliable at judging its own
labor: left to grade itself it will report a theorem as proved when the proof still rests on a
`sorry`, an assumed axiom, or a compiler-trusted decision procedure; it will read "it compiled" as
"it is verified"; it will discharge an obligation it cannot prove by quietly weakening the statement
or replacing the thing under test with a mock; and it will describe all of that in a fluent,
confident report. The gates exist to make each of those outcomes fail closed, and every one of them
is computed by the harness on the files a stage produced — never by the agent, never inferred from
the agent's narrative:

- **`collectAxioms`** decides *established* versus *tainted* from the kernel's own axiom trace, so a
  leftover `sorry`, an opaqued assumption, or `native_decide`'s compiler trust taints the theorem no
  matter what the agent asserts about it — and no matter which stage authored the proof, so a
  FORMALISE-authored proof needs no separate no-smuggle guard: a genuine one counts, one resting on
  `sorryAx` is tainted.
- **the mechanical spec checks** read the *elaborated type*, not source text, so a theorem that is
  vacuous because a measurement is allowed to fail, or an assumption that quietly references a target,
  is caught where a regex could not see it.
- **the pristine-baseline diff** captures every source edit against an untouched checkout, so a
  behaviour-changing "simplification" of the code under verification is visible for review rather
  than silent.
- **the independent judge** runs as a separate session with its own context, so the faithfulness of
  a translation is screened by a party other than the one that wrote it.

This is the code that has to be read, tested, and hardened over time; it is the part where an
undetected weakness would turn a false result into a trusted one, and the part worth the attention.
Keeping it small and free of framework dependencies is what lets that attention go to the gates
themselves rather than to maintaining a framework integration or tracking an SDK's changes.

The rest of the structure follows from the same boundary:

- The agent reads the source directly in the container.
- Run state is numbered checkpoint JSON files and the output repo's git history — no database;
  containers are disposable and runs resume from a checkpoint.
- The filesystem is the container's, isolated by Docker (`--cap-drop all`,
  `--security-opt no-new-privileges`, no bind-mounts) — no virtual filesystem or sandbox shim.
- The agent runs inside that isolated container rather than through a custom tool broker — no
  separate tool-call allow-listing or sanitization layer; the security boundary is the container.
- No RAG DB — knowledge lives as code in repositories for transparency, interpretability,
  enabling sharing over transparent semantics rather than opaque vector embeddings.

Because the trusted core is small and carries no framework dependencies, a future Rust
re-implementation of the harness is bounded work: the gates and the stage sequence port directly,
while the interchangeable agent remains outside that boundary.

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

- **`collectAxioms`** (`lean.check_axioms`) — the authoritative established-vs-tainted verdict, the
  exact kernel axiom set `#print axioms` reports, classified in a harness-owned Lean metaprogram;
- **the mechanical spec checks** (`lean.check_spec_gate`, `lean.legitimacy_check`) — MetaM checks over
  each theorem's *elaborated type* (a spec that assumes its own postcondition; a claim that can fail
  open; a trusted assumption that references a target);
- **`lake build`** — the compile gate;
- **the pristine-baseline git diff** — the audit trail of every source edit.

A stage runs as an AI-agent session; the harness then applies that stage's gate and, if it is not
satisfied, **resumes the session with the gate's feedback** until it passes or a progress-aware
stall trips (`STALL_ROUNDS` consecutive rounds with the *same* failure — a genuinely-improving loop
is never cut off). Each session is bounded by the agent's own `--max-budget-usd`.

## Docker interaction

The toolchain (Rust/Cargo, Charon, Aeneas, Lean/Lake) **and the AI agent itself** (Node + the
agent CLI) live entirely inside a Docker container. There are no bind-mounts: the seed commit is
bundled in at session start, and at the end the run's git branch is fetched back into the target repo.

- The container has its own isolated filesystem — no host paths are exposed. The whole run is ONE
  git repo at `/workspace/repo`: the source is git-initialised at a pristine baseline (so any
  behaviour-preserving edit is captured as a diff) and every stage commits onto branch
  `lusterna/<session>`. Generated artefacts live in that repo's `verification/` subtree, exposed at
  the stable path `/workspace/out` via a symlink.
- Each stage runs as `docker exec … claude -p …` inside the container, with `ANTHROPIC_API_KEY`
  forwarded (never written to a file). Autonomy is `--permission-mode dontAsk` + an explicit tool
  allowlist (`bypassPermissions` is refused as root), and `--max-budget-usd` caps each session.
- The container runs with `--cap-drop all` and `--security-opt no-new-privileges`. Network is
  enabled so Charon can `cargo build` targets whose dependencies are fetched on demand, and so
  the agent can reach the Anthropic API.
