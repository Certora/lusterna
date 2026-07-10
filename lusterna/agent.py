"""Pipeline orchestration: sequencing, loops, per-stage context, and tool wiring.

Stage agents are declared in stages.py; this module imports them, attaches their
tools, and drives them. Each stage runs independently with no shared message
history — stages communicate via the filesystem and deps.progress, not via
conversation context.
"""
import logging
from typing import Any, Callable

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded

from . import checkpoint, lean, telemetry, tools
from .schemas import (
    AbstractInformalSpec, AbstractFormalSpec,
    InformalSpec, FormalSpec, JudgeVerdict, ReconciliationReport,
)
from .stages import (
    doc_infer as _doc_infer, doc_formalise as _doc_formalise, explore as _explore,
    infer as _infer, formalise as _formalise,
    judge as _judge, reconcile as _reconcile, prove as _prove, report as _report,
)
from .schemas import AgentDeps
from . import config

log = logging.getLogger(__name__)


def check_lean(ctx: RunContext[AgentDeps], lean_file: str) -> dict:
    """Run `lake build` in the Lean project and return {success, stderr}.

    The result is stored in progress['lean_build'] and a checkpoint is saved.
    Always call this after writing or modifying any Lean file. On a SUCCESSFUL build this also
    snapshots the compiling impl-spec if it reached a new `sorry` minimum (PROVE's best-state
    preservation — see _record_prove_best); only build-verified states ever count as progress.
    """
    result = lean.check_lean(ctx.deps, lean_file)
    ctx.deps.progress["lean_build"] = result
    ctx.deps.progress["build_seq"] = ctx.deps.progress.get("build_seq", 0) + 1
    if result.get("success"):
        _record_prove_best(ctx.deps)
    _checkpoint(ctx.deps)
    return result


# ── tool registration ───────────────────────────────────────────
# Stage agents are declared in stages.py; their tools — which depend on the
# orchestration helpers in this module — are attached here. TRANSLATE is NOT an
# agent: it runs Charon+Aeneas mechanically on the untouched source (see
# _run_translate_stages), so nothing can rewrite the code under verification.

# FORMALISE is now a structured, tool-less stage: the orchestrator injects its inputs and
# receives a FormalSpec (statements only), then assembles + builds the .lean itself. This
# removes the free-form build loop where the agent used to (against instructions) write and
# debug proofs — proofs are now structurally impossible until PROVE.

_prove.tool(tools.list_files)
_prove.tool(tools.search_output_file)
_prove.tool(tools.read_output_lines)
_prove.tool(tools.read_output_file)
_prove.tool(tools.patch_output_lines)
_prove.tool(tools.write_file)
_prove.tool(check_lean)
_prove.tool(tools.git_commit)
_prove.tool(tools.git_log)

_report.tool(tools.write_file)
_report.tool(tools.git_log)


# ── internal helpers ──────────────────────────────────────────────────────────

def _checkpoint(deps: AgentDeps) -> None:
    checkpoint.save(
        deps.session_id,
        {
            "repo_path": str(deps.repo_path),
            "work_path": str(deps.work_path),
            "container_id": deps.container_id,
            "design_doc": deps.design_doc,
            "progress": deps.progress,
            "git_head": tools.head_sha(deps.container_id),
        },
    )


class _PipelineAborted(Exception):
    pass


def _impl_spec(deps: AgentDeps) -> str:
    """Canonical implementation-spec path — the single file FORMALISE fills and PROVE
    proves. Placed under the Aeneas lib root (lean/<Crate>/) so the existing lakefile
    builds it with no lakefile changes. Deterministic, never agent-chosen."""
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    if not lean_path:
        return ""
    stem = lean_path.rsplit("/", 1)[-1].removesuffix(".lean")
    return f"lean/{stem}/Spec.lean"


def _assemble_impl_spec(deps: AgentDeps, fs: FormalSpec,
                        drop: frozenset[str] = frozenset()) -> list[str]:
    """Write the implementation spec from a structured FormalSpec: the preamble followed by
    one `theorem <name> <signature> := by sorry` per stub. Proofs are added here as `sorry`
    — never by the model — so FORMALISE cannot smuggle in a proof (or a hanging tactic).
    The preamble is passed through stub_proofs as a safety net for any stray theorem.

    Theorems whose name is in *drop* (quarantined — repeatedly un-compilable) are omitted.
    Returns the list of theorem names actually written, so the caller can attribute build
    errors back to specific theorems and track the quarantine set."""
    path = _impl_spec(deps)
    stem = path.split("/")[1]
    preamble = lean.stub_proofs(fs.preamble.rstrip())
    header = "".join(
        f"import {m}\n" for m in ("Aeneas", stem) if f"import {m}" not in preamble
    )
    kept = [t for t in fs.theorems if t.name not in drop]
    body = "\n\n".join(
        f"theorem {t.name} {t.signature.split(':=')[0].strip()} := by sorry"
        for t in kept
    )
    tools.write_out(deps, path, f"{header}{preamble}\n\n{body}\n")
    log.info("Assembled impl spec: %d theorem stub(s)%s at %s", len(kept),
             f" ({len(drop)} quarantined)" if drop else "", path)
    return [t.name for t in kept]


def _build(deps: AgentDeps) -> dict:
    """Run `lake build` on the impl spec and record it as PROVE's progress metric does."""
    result = lean.check_lean(deps, _impl_spec(deps))
    deps.progress["lean_build"] = result
    deps.progress["build_seq"] = deps.progress.get("build_seq", 0) + 1
    _checkpoint(deps)
    return result


def _sorry_count(deps: AgentDeps) -> int:
    """Remaining `sorry` in the implementation spec — PROVE's progress metric.
    Returns -1 if the file can't be read, so the caller never mistakes it for done."""
    content = tools.read_out(deps, _impl_spec(deps))
    return -1 if content.startswith("ERROR:") else content.count("sorry")


def _record_prove_best(deps: AgentDeps) -> None:
    """Snapshot the impl spec as PROVE's best state iff it COMPILES (caller checked) and reached
    a new `sorry` minimum. Only build-verified states count: a failing tactic removes a `sorry`
    but does not compile, so raw sorry-count is not progress — a compiling snapshot is. PROVE
    restores this at the end, so it always finalizes on its best verified state, never a later
    broken edit. progress['prove_best'] = {'sorry': n, 'spec': <content>}."""
    spec = tools.read_out(deps, _impl_spec(deps))
    if spec.startswith("ERROR:"):
        return
    n = spec.count("sorry")
    best = deps.progress.get("prove_best")
    if best is None or n < best["sorry"]:
        deps.progress["prove_best"] = {"sorry": n, "spec": spec}
        log.info("PROVE: new best compiling spec — %d sorry remaining", n)


def _translation_text(deps: AgentDeps) -> str:
    """Concatenated Lean source of the whole Aeneas translation — the material the spec
    stages reason over. The source is immutable, so this is a faithful image of the crate.
    The tool chooses which properties to state; it is not scoped to a pre-picked unit."""
    parts = []
    for rel in deps.progress.get("aeneas", {}).get("lean_files", []):
        text = tools.read_out(deps, rel)
        if not text.startswith("ERROR:"):
            parts.append(text)
    return "\n\n".join(parts)


def _footprint(deps: AgentDeps) -> dict:
    """Compute the footprint of the current implementation spec: the translation defs its
    theorems (statements + proofs) reference transitively, and which Aeneas holes fall
    inside it.

    This is the SOLE role of the 'unit' concept — a stated property is soundly grounded
    iff no hole lies in its footprint. Holes elsewhere in the crate are irrelevant to it.
    Recomputed after FORMALISE and after PROVE (proofs may pull in more defs). Stored in
    progress['footprint'] = {defs, holes_in_footprint}."""
    translation = _translation_text(deps)
    spec = tools.read_out(deps, _impl_spec(deps))
    holes = deps.progress.get("aeneas", {}).get("holes", [])
    if translation.startswith("ERROR:") or spec.startswith("ERROR:"):
        # Can't compute — take the conservative (never-claim-sound) direction.
        fp = {"defs": [], "holes_in_footprint": list(holes)}
    else:
        roots = lean.referenced_defs(spec, translation)
        defs = lean.call_closure(translation, roots)
        fp = {"defs": defs, "holes_in_footprint": [h for h in holes if h in defs]}
    deps.progress["footprint"] = fp
    hif = fp["holes_in_footprint"]
    if hif:
        log.warning("Footprint reaches %d untranslated hole(s) %s — properties touching "
                    "them are NOT soundly grounded", len(hif), hif)
    else:
        log.info("Footprint: %d translation def(s), no Aeneas holes inside — "
                 "properties soundly grounded", len(fp["defs"]))
    _checkpoint(deps)
    return fp


def _pipeline_briefing(deps: AgentDeps) -> str:
    """Return a structured context block describing pipeline state so far.

    Prepended to every stage's runtime prompt so each stage agent understands
    the overall goal, what has been completed, and key findings from prior stages.
    """
    p = deps.progress

    # Completed stages
    stage_flags = [
        ("abstract_informal_spec", "DOC-INFER"),
        ("abstract_formal_spec",   "DOC-FORMALISE"),
        ("aeneas",                 "EXPLORE+TRANSLATE"),
        ("informal_spec",          "INFER"),
        ("formal_spec",            "FORMALISE"),
        ("verdict",                "SPEC-JUDGE"),
        ("reconciliation",         "RECONCILE"),
        ("proofs_done",            "PROVE"),
    ]
    done = [label for key, label in stage_flags if key in p]

    fp = p.get("footprint")
    fp_line = ""
    if fp is not None:
        hif = fp.get("holes_in_footprint", [])
        fp_line = (
            f"Property footprint: {len(fp.get('defs', []))} translation def(s); "
            + ("no Aeneas holes inside — proven properties are soundly grounded"
               if not hif else
               f"⚠ {len(hif)} untranslated hole(s) INSIDE the footprint ({hif}) — "
               f"properties touching them are NOT soundly grounded")
        )
    holes = (p.get("aeneas") or {}).get("holes", [])

    lines = [
        "## Pipeline context",
        "Goal: formally verify the properties the design document describes against the "
        "Aeneas-translated crate. What to specify is the tool's choice; a property may be "
        "about one function or span several functions and types. The source is never "
        "modified, so the translation is a faithful image of the real code.",
        f"Design document (excerpt):\n{deps.design_doc[:600].rstrip()}",
        "",
        f"Completed stages: {', '.join(done) if done else 'none yet'}",
    ]
    if holes:
        lines.append(f"Aeneas holes (untranslated defs) in the crate: {holes}")
    if fp_line:
        lines.append(fp_line)
    ax = p.get("axioms")
    if ax is not None:
        tainted = ax.get("tainted", [])
        lines.append(
            f"Axiom check (Lean `#print axioms`, authoritative): "
            f"{len(ax.get('clean', []))} theorem(s) genuinely established (no sorryAx), "
            f"{len(tainted)} still resting on sorry"
            + (f" — tainted: {tainted}" if tainted else "")
        )

    # Artefact inventory
    artefacts = []
    if "aeneas" in p:
        lean_path = p["aeneas"].get("lean_path", "")
        if lean_path:
            artefacts.append(lean_path)
    for key, path in [
        ("abstract_informal_spec", "specs/abstract_informal_spec.json"),
        ("abstract_formal_spec",   "specs/abstract_formal_spec.lean"),
        ("informal_spec",          "specs/informal_spec.json"),
        ("formal_spec",            _impl_spec(deps)),
    ]:
        if key in p and path:
            artefacts.append(path)
    if "reconciliation" in p:
        artefacts.append("specs/reconciliation.json")
    if artefacts:
        lines.append(
            f"Key artefacts (all in /workspace/out — use read_output_file): "
            + ", ".join(artefacts)
        )

    # Spec-judge verdict
    verdict = p.get("verdict")
    if verdict is not None:
        defects = verdict.get("defects", [])
        if defects:
            lines.append(
                f"\nSpec-judge: {len(defects)} open defect(s): "
                + ", ".join(f"{d['theorem']}[{d['kind']}]" for d in defects)
            )
        else:
            lines.append("\nSpec-judge: no defects (spec approved ✓)")

    # Reconciliation summary
    rc = p.get("reconciliation")
    if rc:
        discrepancies = rc.get("discrepancies", [])
        critical = [d for d in discrepancies if d.get("severity") == "critical"]
        obligations = rc.get("refinement_obligations", [])
        lines.append(
            f"\nReconciliation: {len(discrepancies)} discrepancy/ies "
            f"({len(critical)} critical), {len(obligations)} refinement obligation(s)"
        )
        for d in critical:
            lines.append(f"  [CRITICAL {d.get('kind','')}] {d.get('description','')[:120]}")

    lines.append("")   # trailing newline before stage-specific prompt
    return "\n".join(lines) + "\n"


async def _run_stage(agent: Agent, prompt: str, deps: AgentDeps, label: str,
                     stop_check: Callable[[AgentDeps, Any], bool] | None = None,
                     request_limit: Any = "default") -> Any:
    """Run one stage agent. Each stage starts with no prior history.

    Stages communicate via the filesystem and deps.progress, not via conversation
    context — so no history is passed in or accumulated across stages. When stop_check
    is given it is polled on each graph node (deps, node); returning True ends the run early.
    `request_limit` overrides config.REQUEST_LIMIT when not "default".
    Re-raises UnexpectedModelBehavior; judge stages catch it locally.
    """
    log.info("─── Stage: %s ───", label)
    telemetry.stage.reset()
    deps.message_history = []
    full_prompt = _pipeline_briefing(deps) + prompt
    from pydantic_ai.usage import UsageLimits
    rl = config.REQUEST_LIMIT if request_limit == "default" else request_limit
    async with agent.iter(
        full_prompt, deps=deps,
        usage_limits=UsageLimits(request_limit=rl),
    ) as run:
        async for node in run:
            if stop_check and stop_check(deps, node):
                break
        result = run.result
    log.info(
        "Stage %s complete — session total=%d/%s",
        label, telemetry.session.total(), telemetry.budget or "∞",
    )
    return result


# ── pipeline sub-functions ────────────────────────────────────────────────────

async def _run_doc_stages(deps: AgentDeps, resume_note: str) -> str:
    """Run DOC-INFER then DOC-FORMALISE (once each) from the design document alone."""
    completed = set(deps.progress.keys())

    if "abstract_informal_spec" not in completed:
        result = await _run_stage(
            _doc_infer,
            f"Begin DOC-INFER. Derive the abstract informal specification from this "
            f"design document:\n\n{deps.design_doc}" + resume_note,
            deps, "DOC-INFER",
        )
        resume_note = ""
        if not (result and result.output):
            log.warning("DOC-INFER produced no output — proceeding without abstract spec")
            return resume_note
        spec: AbstractInformalSpec = result.output
        deps.progress["abstract_informal_spec"] = spec.model_dump()
        tools.write_out(deps, "specs/abstract_informal_spec.json", spec.model_dump_json(indent=2))
        _checkpoint(deps)

    if "abstract_formal_spec" not in completed and "abstract_informal_spec" in deps.progress:
        inf_json = AbstractInformalSpec(**deps.progress["abstract_informal_spec"]).model_dump_json(indent=2)
        result = await _run_stage(
            _doc_formalise,
            f"Proceed to DOC-FORMALISE. Produce Lean 4 abstract theorem stubs from this "
            f"abstract informal spec:\n\n{inf_json}" + resume_note,
            deps, "DOC-FORMALISE",
        )
        resume_note = ""
        if result and result.output:
            formal: AbstractFormalSpec = result.output
            deps.progress["abstract_formal_spec"] = formal.model_dump()
            tools.write_out(
                deps, "specs/abstract_formal_spec.lean",
                formal.lean_definitions + "\n\n" + formal.lean_theorem_stubs,
            )
            tools.commit(deps.container_id, "feat(spec): abstract formal specification", glob="specs/")
            _checkpoint(deps)
        else:
            log.warning("DOC-FORMALISE produced no output")

    return resume_note


async def _run_translate_stages(deps: AgentDeps, resume_note: str) -> str:
    """Run EXPLORE, TRANSLATE, and INFER (skipped if already in progress).

    Raises _PipelineAborted if TRANSLATE or INFER produce no output.
    """
    completed = set(deps.progress.keys())

    if "aeneas" not in completed:
        sources = tools.read_repo_sources(deps)
        explore_result = await _run_stage(
            _explore,
            f"Rust repository at {deps.repo_path}. Analyse the following sources:\n\n"
            f"{sources}" + resume_note,
            deps, "EXPLORE",
        )
        if explore_result and explore_result.output:
            deps.progress["explore"] = explore_result.output.model_dump()
        _checkpoint(deps)
        resume_note = ""

    if "aeneas" not in completed:
        # TRANSLATE is mechanical and the source is IMMUTABLE: run Charon+Aeneas on the
        # crate exactly as written. Untranslatable constructs become explicit `sorry`
        # holes, never silent rewrites. A hard failure (no output at all) aborts — the
        # fix is the user's, outside the trust boundary.
        log.info("─── Stage: TRANSLATE (mechanical, source immutable) ───")
        entry = deps.progress.get("explore", {}).get("entry_file", "src/lib.rs")
        result = lean.run_aeneas(deps, entry)
        if not result.get("success"):
            raise _PipelineAborted(
                "TRANSLATE failed — Charon/Aeneas produced no output for the untouched "
                f"source ({result.get('charon_errors') or result.get('aeneas_errors') or 'unknown'}). "
                "The source is not modified; expose the logic via a lib target or "
                "simplify the untranslatable construct, then re-run."
            )
        deps.progress["aeneas"] = result
        if result.get("holes"):
            log.info("TRANSLATE: %d untranslated hole(s) left as sorry: %s",
                     len(result["holes"]), ", ".join(result["holes"]))
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())

    if "informal_spec" not in completed:
        translation = _translation_text(deps)
        abstract_informal = tools.read_out(deps, "specs/abstract_informal_spec.json")
        infer_files = f"### Aeneas-translated crate\n{translation}"
        if not abstract_informal.startswith("ERROR:"):
            infer_files += f"\n\n### specs/abstract_informal_spec.json\n{abstract_informal}"
        infer_result = await _run_stage(
            _infer,
            f"Proceed to INFER. Derive a structured InformalSpec of the behaviour the "
            f"design document describes, as realised by the crate, from the following:"
            f"\n\n{infer_files}" + resume_note,
            deps, "INFER",
        )
        if infer_result and infer_result.output:
            spec: InformalSpec = infer_result.output
            deps.progress["informal_spec"] = spec.model_dump()
            tools.write_out(deps, "specs/informal_spec.json", spec.model_dump_json(indent=2))
            tools.commit(deps.container_id, "feat(spec): informal specification", glob="specs/")
        _checkpoint(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "informal_spec" not in completed:
            raise _PipelineAborted("Informal spec inference failed.")

    return resume_note


def _read_spec_files(deps: AgentDeps) -> str:
    """Read all spec files needed by judge and reconcile stages and return as a formatted block."""
    paths = [
        "specs/abstract_formal_spec.lean",
        "specs/abstract_informal_spec.json",
        "specs/informal_spec.json",
    ]
    if impl := _impl_spec(deps):
        paths.append(impl)
    parts = []
    for path in paths:
        content = tools.read_out(deps, path)
        if not content.startswith("ERROR:"):
            parts.append(f"### {path}\n{content}")
    return "\n\n".join(parts)


def _formalise_inputs(deps: AgentDeps) -> str:
    """Static inputs injected into every FORMALISE round: the translated crate, the
    informal spec, and the abstract formal spec (if present)."""
    block = f"### Aeneas-translated crate\n{_translation_text(deps)}\n\n"
    for path in ("specs/informal_spec.json", "specs/abstract_formal_spec.lean"):
        content = tools.read_out(deps, path)
        if not content.startswith("ERROR:"):
            block += f"### {path}\n{content}\n\n"
    return block


class SpecPhase:
    """The FORMALISE → assemble → build → SPEC-JUDGE loop.

    Each round FORMALISE (re)states the properties as a FormalSpec; the orchestrator assembles
    them as `:= by sorry` stubs (so no proof can be smuggled in) and builds, validating only
    that the STATEMENTS typecheck. Build errors are attributed per-theorem and repeat offenders
    are quarantined; spec-judge defects are fed back. Ends when the spec builds AND has no
    defects, progress stalls, or the round cap is hit. The loop state (quarantine set,
    per-theorem fail streaks, last compiling signatures, previous defects/build errors, carried
    feedback) lives in fields rather than threaded locals."""

    MAX_ROUNDS = 10        # hard cap on FORMALISE+judge rounds
    QUARANTINE_AFTER = 3   # drop a theorem after this many rounds failing to compile

    def __init__(self, deps: AgentDeps, resume_note: str):
        self.deps = deps
        self.resume_note = resume_note
        self.impl_spec = _impl_spec(deps)
        self.base_inputs = _formalise_inputs(deps)
        self.dropped: set[str] = set()
        self.fail_streak: dict[str, int] = {}
        self.last_good_sigs: dict[str, str] = {}
        self.prev_defects: frozenset | None = None
        self.prev_build_err: str | None = None
        self.feedback = ""     # revision feedback carried between rounds (build errors / defects)
        self.best_spec: str | None = None   # last spec content that compiled (for best-restore)
        self.attempt = 0

    async def run(self) -> str:
        """Run the loop and return the (possibly cleared) resume_note. Fail-fasts if the spec
        never compiled — RECONCILE and PROVE must not run on an unverifiable spec."""
        if "verdict" in self.deps.progress:                 # resuming past this phase
            if "footprint" not in self.deps.progress:
                _footprint(self.deps)
            return self.resume_note

        while self.attempt < self.MAX_ROUNDS:
            fs = await self._formalise()
            if fs is None:
                break
            kept = _assemble_impl_spec(self.deps, fs, drop=frozenset(self.dropped))
            self.deps.progress["formal_spec"] = True
            if not kept:
                log.warning("FORMALISE: every theorem is quarantined or none was produced — "
                            "nothing left to verify, stopping spec loop")
                break
            if not _build(self.deps).get("success"):
                if not self._handle_build_failure(fs):
                    break
                self.attempt += 1
                continue
            # A compiling spec: snapshot it (best-restore) and remember its signatures (to later
            # attribute a break), then judge.
            self.best_spec = tools.read_out(self.deps, self.impl_spec)
            self.last_good_sigs = {t.name: t.signature for t in fs.theorems
                                   if t.name not in self.dropped}
            if not await self._judge():
                break
            self.attempt += 1
        else:
            log.warning("Spec loop hit hard cap of %d rounds", self.MAX_ROUNDS)

        # If the loop ended on a NON-compiling spec but an earlier revision DID compile, restore
        # that last compiling spec rather than aborting: spec-judge defects don't gate the
        # pipeline, so a compiling (even if judge-imperfect) spec is verifiable and must not be
        # thrown away just because the final revision broke the build. (Mirrors PROVE's restore.)
        if not self.deps.progress.get("lean_build", {}).get("success") and self.best_spec:
            tools.write_out(self.deps, self.impl_spec, self.best_spec)
            _build(self.deps)
            log.info("FORMALISE: restored the last compiling spec after a non-compiling final round")

        if not self.deps.progress.get("lean_build", {}).get("success"):
            raise _PipelineAborted(
                f"FORMALISE could not produce a spec whose statements compile "
                f"(after up to {self.MAX_ROUNDS} rounds) — cannot verify")
        _footprint(self.deps)   # which Aeneas holes (if any) actually touch the stated properties
        return self.resume_note

    async def _formalise(self) -> "FormalSpec | None":
        """One FORMALISE emit (ONE-SHOT, no tools — the whole translation is injected, the build
        is the gate). Returns the FormalSpec, or None to stop the loop."""
        if self.attempt == 0:
            instruction = (
                "FORMALISE: return a FormalSpec (preamble + statement-only theorems) "
                "capturing the properties that matter — the behaviour the design document "
                "describes, as realised by the crate. You write no proofs.")
        else:
            cur = tools.read_out(self.deps, self.impl_spec)
            instruction = (
                f"FORMALISE revision (round {self.attempt + 1}). Return an updated FormalSpec. "
                f"{self.feedback}\n\n### current assembled spec\n{cur}")
        try:
            fr = await _run_stage(
                _formalise, f"{instruction}\n\n{self.base_inputs}" + self.resume_note,
                self.deps, f"FORMALISE (round {self.attempt + 1})")
        except UnexpectedModelBehavior as e:
            # A retry blow-up must never hard-crash the pipeline; the compile gate turns this
            # into a graceful abort-with-notes if no building spec was produced.
            log.warning("FORMALISE failed after retries: %s — stopping spec loop", e)
            return None
        self.resume_note = ""
        if not (fr and fr.output):
            log.warning("FORMALISE produced no output — stopping spec loop")
            return None
        return fr.output

    def _handle_build_failure(self, fs: FormalSpec) -> bool:
        """Attribute the build errors to theorems, quarantine repeat offenders, and set targeted
        revision feedback. Returns False if no progress is possible (stop), True to keep going."""
        err = self.deps.progress["lean_build"].get("stderr", "")
        attr = lean.attribute_errors(tools.read_out(self.deps, self.impl_spec), err)
        failing, preamble_errs, other_errs = attr["failing"], attr["preamble"], attr["unattributed"]
        cur_sigs = {t.name: t.signature for t in fs.theorems if t.name not in self.dropped}

        # Culprits: prefer errors attributed to a specific theorem; if the break can't be pinned
        # (and isn't a preamble error), fall back to the theorems changed since the last compile.
        changed = [n for n, s in cur_sigs.items() if self.last_good_sigs.get(n) != s]
        culprits = list(failing) if failing else ([] if preamble_errs else changed)

        newly_dropped = []
        for name in culprits:
            self.fail_streak[name] = self.fail_streak.get(name, 0) + 1
            if self.fail_streak[name] >= self.QUARANTINE_AFTER:
                self.dropped.add(name); newly_dropped.append(name)
        # Only call a theorem "compiling" if it is unchanged from a spec that DID compile and is
        # not a culprit — never infer compilation from an absent error.
        ok_names = [n for n in cur_sigs
                    if n not in culprits and self.last_good_sigs.get(n) == cur_sigs[n]]
        for name in ok_names:
            self.fail_streak[name] = 0
        if newly_dropped:
            self.deps.progress["dropped_theorems"] = sorted(self.dropped)
            log.warning("FORMALISE: quarantined %d theorem(s) after %d failed rounds: %s",
                        len(newly_dropped), self.QUARANTINE_AFTER, newly_dropped)

        if err == self.prev_build_err and not newly_dropped:
            log.warning("FORMALISE: identical build errors and nothing to quarantine — no "
                        "progress, stopping spec loop")
            return False
        self.prev_build_err = err
        log.info("FORMALISE round %d: %d attributed-failing, %d culprit(s), %d known-ok, %d dropped",
                 self.attempt + 1, len(failing), len(culprits), len(ok_names), len(self.dropped))
        self.feedback = self._build_feedback(failing, preamble_errs, other_errs, changed, ok_names, err)
        return True

    def _build_feedback(self, failing, preamble_errs, other_errs, changed, ok_names, err) -> str:
        """Assemble the targeted revision message: per-theorem errors, keep-verbatim list, drops."""
        parts = ["The assembled spec did NOT compile. You still write NO proofs (bodies are "
                 "`:= by sorry`)."]
        if preamble_errs:
            parts.append("PREAMBLE errors — fix the imports/helper defs in `preamble`:\n"
                         + "\n".join(preamble_errs))
        if failing:
            parts.append(
                "These theorem STATEMENTS do not typecheck — restate each so it compiles "
                "(same intent; use the EXACT Aeneas names/types from the injected translation), "
                "or state it more simply:\n"
                + "\n\n".join(f"  • {n}:\n{msg}" for n, msg in failing.items()))
        elif not preamble_errs:
            parts.append(
                "The build failed but the error could not be pinned to one theorem. It broke "
                "after these theorems changed — revert them to a form that compiled, or state "
                f"them more simply: {', '.join(changed) or '(unknown)'}\nBuild output:\n" + err[-1500:])
        if other_errs:
            parts.append("Other build errors:\n" + "\n".join(other_errs))
        if ok_names:
            parts.append("These already COMPILE — keep them EXACTLY as they are: " + ", ".join(ok_names))
        if self.dropped:
            parts.append("DROPPED as un-stateable — do NOT re-introduce these (they are reported "
                         "as not formalised): " + ", ".join(sorted(self.dropped)))
        return "\n\n".join(parts)

    async def _judge(self) -> bool:
        """Judge the compiling statements; store the verdict and set defect feedback. Returns
        False to stop the loop (approved / no-progress / error), True to keep revising."""
        judge_files = f"### Aeneas-translated crate\n{_translation_text(self.deps)}\n\n"
        for path in ("specs/informal_spec.json", self.impl_spec):
            content = tools.read_out(self.deps, path)
            if not content.startswith("ERROR:"):
                judge_files += f"### {path}\n{content}\n\n"
        # Quarantine handshake: dropped theorems are gone for good — the judge must not re-demand
        # them as missing_coverage, or judge and compiler fight forever.
        dropped_note = (
            "\n\nNOTE: the following properties could NOT be stated in a way that compiles and "
            "were dropped from the spec — do NOT report them (or their absence) as a defect or "
            f"missing_coverage: {', '.join(sorted(self.dropped))}." if self.dropped else "")
        try:
            sj = await _run_stage(
                _judge,
                "SPEC-JUDGE: list every defect in the implementation spec's theorem statements "
                "(ignore sorry proofs); return an empty list if it is sound."
                + dropped_note + f"\n\n{judge_files}",
                self.deps, f"SPEC-JUDGE (round {self.attempt + 1})")
        except UnexpectedModelBehavior as e:
            log.warning("Spec judge failed after retries: %s — stopping spec loop", e)
            return False
        if not (sj and sj.output):
            log.warning("Spec judge produced no output — stopping spec loop")
            return False
        sv: JudgeVerdict = sj.output
        self.deps.progress["verdict"] = sv.model_dump()
        _checkpoint(self.deps)
        if not sv.defects:
            log.info("Spec-judge round %d: no defects — spec approved", self.attempt + 1)
            return False
        log.info("Spec-judge round %d: %d defect(s): %s", self.attempt + 1, len(sv.defects),
                 ", ".join(f"{d.theorem}[{d.kind}]" for d in sv.defects))
        defect_sig = frozenset((d.theorem, d.kind) for d in sv.defects)
        if defect_sig == self.prev_defects:
            log.warning("Spec-judge: same defects as last round — no progress, stopping")
            return False
        self.prev_defects = defect_sig
        defect_lines = "\n".join(
            f"  - {d.theorem} [{d.kind}]: {d.detail} → fix: {d.fix}" for d in sv.defects)
        self.feedback = (
            f"The spec-judge found {len(sv.defects)} defect(s) in the STATEMENTS. Revise to fix "
            f"EVERY one below — do not skip or deem any redundant. If a fix asks for a theorem, "
            f"add it as a stub (state it explicitly even if another theorem entails it). Change "
            f"nothing else.\n{defect_lines}")
        return True


async def _run_reconcile_phase(deps: AgentDeps) -> None:
    """Run RECONCILE (skipped if already done or no abstract spec)."""
    completed = set(deps.progress.keys())

    if "abstract_formal_spec" not in completed or "reconciliation" in completed:
        return

    rc_result = await _run_stage(
        _reconcile,
        "RECONCILE: compare abstract spec vs implementation spec. "
        "Classify every discrepancy and produce refinement obligations for critical ones."
        + f"\n\n{_read_spec_files(deps)}",
        deps,
        "RECONCILE",
    )
    if rc_result and rc_result.output:
        rc: ReconciliationReport = rc_result.output
        deps.progress["reconciliation"] = rc.model_dump()
        tools.write_out(deps, "specs/reconciliation.json", rc.model_dump_json(indent=2))
        tools.commit(
            deps.container_id,
            "feat(spec): reconciliation — abstract vs impl spec",
            glob="specs/",
        )
        critical = [d for d in rc.discrepancies if d.severity == "critical"]
        log.info(
            "Reconcile: aligned=%d discrepancies=%d critical=%d refinement_obligations=%d",
            len(rc.aligned), len(rc.discrepancies),
            len(critical), len(rc.refinement_obligations),
        )
        if critical:
            log.warning(
                "RECONCILE found %d CRITICAL discrepancy/ies: %s",
                len(critical),
                [d.kind + ": " + d.description[:60] for d in critical],
            )
    _checkpoint(deps)


class ProvePhase:
    """PROVE against the real build oracle: the agent edits proof bodies and calls check_lean
    (whose output carries the actual Lean diagnostics — `unsolved goals` with the remaining goal
    state, type errors) to prove goal-directed, with no lean-lsp tooling. The orchestrator owns
    the objective guards: a compile gate before any effort; a genuine-progress stop keyed on the
    BUILD-VERIFIED sorry minimum (a failing tactic drops the raw count but breaks the build, so
    only a successful build counts); best-state RESTORE (finalize on the best compiling spec
    seen, never a later broken edit); one authoritative final build; and the `#print axioms`
    gate + implementation-vs-abstract partition of the established theorems. The stop's tracking
    (best verified minimum, stall/warm-up turns) lives in fields."""

    STALL = 15    # stop after this many turns with no new verified minimum (after the first proof)
    WARMUP = 25   # stop if no verified proof lands within this many turns

    def __init__(self, deps: AgentDeps):
        self.deps = deps
        self.impl_spec = _impl_spec(deps)
        self.best: int | None = None   # best build-verified sorry count seen
        self.stall = 0                 # turns since a new verified minimum
        self.improved = False          # has any verified proof landed (below the baseline)?
        self.turns = 0

    async def run(self) -> None:
        deps = self.deps
        if "proofs_done" in deps.progress:
            log.info("PROVE already complete — skipping proof stage")
            return
        # Airtight compile gate: NEVER spend proof effort on a spec that does not build. The spec
        # phase fail-fasts on a fresh run, but a RESUMED session can re-enter with a spec that
        # only ever built in a previous container — so re-verify with a real build now.
        if not _build(deps).get("success"):
            raise _PipelineAborted(
                "PROVE not started — the implementation spec does not compile "
                "(FORMALISE did not produce a spec whose statements build)")
        # Baseline best-state = the entry (all-sorry) spec, which just built — the floor PROVE
        # restores to if no proof survives, so it can never abort away a compiling spec.
        deps.progress.pop("prove_best", None)
        _record_prove_best(deps)

        try:
            await _run_stage(_prove, self._prompt(), deps, "PROVE",
                             request_limit=config.PROVE_REQUEST_LIMIT, stop_check=self._stop)
        except UnexpectedModelBehavior as e:
            log.warning("PROVE stage failed after retries: %s", e)
        except UsageLimitExceeded as e:
            # Hit the request-count backstop — finalize with whatever it proved (the final build
            # + axiom gate below still run).
            log.warning("PROVE hit the request-limit backstop (%s) — finalizing", e)
        _checkpoint(deps)
        self._restore_best()
        # The agent may stop before committing — commit here so the proven state lands in git.
        tools.commit(deps.container_id, "stage/prove: proof attempts", glob="lean/")
        # Authoritative final build. With the restore above this is the best verified state, so it
        # builds — the abort is a last-resort invariant check.
        if not _build(deps).get("success"):
            raise _PipelineAborted("PROVE left the spec in a non-compiling state")
        deps.progress["proofs_done"] = True
        _footprint(deps)   # cheap pre-oracle estimate — proofs may reference defs the stubs did not
        self._record_axioms()

    def _prompt(self) -> str:
        rc = self.deps.progress.get("reconciliation", {})
        critical = [d for d in rc.get("discrepancies", []) if d.get("severity") == "critical"]
        rc_obligations = rc.get("refinement_obligations", [])
        note = ""
        if rc_obligations:
            note = (f"\n\nRECONCILIATION NOTE: {len(rc_obligations)} refinement obligation(s) were "
                    "generated to bridge the impl spec to the abstract spec. Their sorry stubs are "
                    "in specs/reconciliation.json. You may add them to the spec file and attempt "
                    "to prove them.")
        if critical:
            lines = "\n".join(f"  [{d['kind']}] {d['description'][:100]}" for d in critical)
            note += (f"\n\nCRITICAL ({len(critical)} — do NOT paper over):\n{lines}\n"
                     "These indicate a potential bug in the implementation or a mis-stated "
                     "theorem. Leave the corresponding obligations unproved and note them clearly.")
        return (
            f"Proceed to PROVE. Fill in proofs for as many `sorry` theorems in `{self.impl_spec}` "
            f"as you can, WITHOUT changing any statement. Work ONE theorem at a time, easiest "
            f"first (base cases, concrete values, simple bounds). For each: search_output_file to "
            f"locate it, read_output_lines to read its block, patch_output_lines to replace ONLY "
            f"the proof body, then check_lean to build. The build output shows the REAL errors — "
            f"an incomplete proof reports `unsolved goals` with the remaining goal state, so read "
            f"it to choose the next tactic. If a proof fails, revert that theorem to `:= by sorry` "
            f"and move on — leaving hard theorems as `sorry` is expected and honest. Make sure the "
            f"file still compiles when you finish, then git_commit." + note)

    def _stop(self, deps: AgentDeps, node: Any) -> bool:
        """Polled once per LLM turn (model-request node). Metric is the BUILD-VERIFIED sorry
        minimum (progress['prove_best'], updated only on a successful build), NOT the raw file
        count. Stop at 0, after STALL turns with no new verified minimum, or WARMUP turns with no
        verified proof at all (bounds an agent that edits blindly / never builds cleanly)."""
        if not Agent.is_model_request_node(node):
            return False
        self.turns += 1
        best = (deps.progress.get("prove_best") or {}).get("sorry")
        if best is None:
            return False
        if self.best is None or best < self.best:
            if self.best is not None:      # a verified decrease below the entry baseline
                self.improved = True
            self.best, self.stall = best, 0
        elif self.improved:
            self.stall += 1
        if self.improved:
            stalled, cap, phase = self.stall, self.STALL, "no-improvement"
        else:
            stalled, cap, phase = self.turns, self.WARMUP, "no verified proof"
        log.info("PROVE progress: best verified sorry=%d (%s %d/%d turns)", best, phase, stalled, cap)
        return best == 0 or stalled >= cap

    def _restore_best(self) -> None:
        """Restore the best VERIFIED spec over any later broken/blind edit (worst case: the entry
        all-sorry baseline)."""
        best = self.deps.progress.get("prove_best")
        if best is not None and tools.read_out(self.deps, self.impl_spec) != best["spec"]:
            tools.write_out(self.deps, self.impl_spec, best["spec"])
            log.info("PROVE: restored best verified spec (%d sorry) over the final edit state",
                     best["sorry"])

    def _record_axioms(self) -> None:
        """`#print axioms` (standard-axioms-only) + partition the established theorems: a theorem
        only VERIFIES THE IMPLEMENTATION if its statement references a real Aeneas translation def
        (referenced_defs non-empty); established theorems about abstract preamble defs alone are
        helper lemmas, not verification of the code — a mechanical distinction, not a judgment."""
        deps = self.deps
        ax = lean.check_axioms(deps, self.impl_spec)
        translation = _translation_text(deps)
        spec_text = tools.read_out(deps, self.impl_spec)
        impl_verified, abstract_only = [], []
        for name in ax["clean"]:
            stmt = lean.theorem_statement(spec_text, name)
            (impl_verified if stmt and lean.referenced_defs(stmt, translation)
             else abstract_only).append(name)
        deps.progress["axioms"] = {"clean": ax["clean"], "tainted": ax["tainted"],
                                   "impl_verified": impl_verified, "abstract_only": abstract_only}
        _checkpoint(deps)
        if ax["clean"] and not impl_verified:
            log.warning("PROVE: ⚠ CRITICAL — %d theorem(s) established but NONE reference the "
                        "implementation; 0 properties of the code are verified. Established are "
                        "abstract helper lemmas only: %s", len(ax["clean"]), abstract_only)
        log.info("PROVE complete — %d sorry remaining; established (standard axioms only): %d/%d "
                 "theorem(s) — %d verify the implementation %s, %d abstract-only %s; tainted=%s",
                 max(_sorry_count(deps), 0), len(ax["clean"]),
                 len(ax["clean"]) + len(ax["tainted"]), len(impl_verified), impl_verified,
                 len(abstract_only), abstract_only, ax["tainted"])


async def _run_report(deps: AgentDeps, resume_note: str) -> str:
    """Run the REPORT stage and concatenate section files into VERIFICATION_REPORT.md."""
    spec_defects     = deps.progress.get("verdict", {}).get("defects", [])
    sorry_remaining  = max(_sorry_count(deps), 0)
    holes            = deps.progress.get("aeneas", {}).get("holes", [])
    fp               = deps.progress.get("footprint", {})
    holes_in_fp      = fp.get("holes_in_footprint", [])
    axioms           = deps.progress.get("axioms", {})
    ax_clean         = axioms.get("clean", [])
    ax_tainted       = axioms.get("tainted", [])
    ax_impl          = axioms.get("impl_verified", [])
    ax_abstract      = axioms.get("abstract_only", [])
    rc               = deps.progress.get("reconciliation", {})
    rc_critical     = sum(1 for d in rc.get("discrepancies", []) if d.get("severity") == "critical")
    rc_obligations_n = len(rc.get("refinement_obligations", []))

    report_files = _read_spec_files(deps)
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    if lean_path:
        lean_content = tools.read_out(deps, lean_path)
        if not lean_content.startswith("ERROR:"):
            report_files = f"### {lean_path}\n{lean_content}\n\n{report_files}"
    rc_json = tools.read_out(deps, "specs/reconciliation.json")
    if not rc_json.startswith("ERROR:"):
        report_files += f"\n\n### specs/reconciliation.json\n{rc_json}"

    report_result = await _run_stage(
        _report,
        f"Proceed to REPORT. "
        f"Verification approach: the tool chose which properties to specify over the whole "
        f"translated crate; a property is soundly grounded only if no Aeneas hole lies in "
        f"its footprint. Footprint: {len(fp.get('defs', []))} translation def(s) — "
        + ("no holes inside, properties soundly grounded. "
           if not holes_in_fp else
           f"NOT fully verified: {len(holes_in_fp)} hole(s) INSIDE the footprint "
           f"({holes_in_fp}) taint the properties touching them. ")
        + f"Translation: source UNMODIFIED (Aeneas ran on the code as written); "
        f"untranslated holes in crate: {', '.join(holes) if holes else 'none'}. "
        f"Spec-judge: {'approved (no defects)' if not spec_defects else str(len(spec_defects)) + ' unresolved defect(s)'}. "
        f"Proofs: {sorry_remaining} theorem(s) remain as `sorry`. AUTHORITATIVE soundness "
        f"(Lean `#print axioms`, standard axioms only): {len(ax_clean)} theorem(s) kernel-"
        f"established. Of those, the HEADLINE RESULT is the {len(ax_impl)} that actually VERIFY "
        f"THE IMPLEMENTATION (reference an Aeneas-translated def): {ax_impl or 'NONE'}"
        + (f". ⚠ CRITICAL: {len(ax_clean)} theorem(s) were proved but NONE reference the "
           f"implementation — 0 properties of the real code are verified; the established "
           f"theorems are abstract helper lemmas ({ax_abstract}). Report this prominently as the "
           f"headline, NOT as a success. " if ax_clean and not ax_impl else
           (f"; the other {len(ax_abstract)} established are abstract helper lemmas "
            f"({ax_abstract}) — report them separately, not as code verification. " if ax_abstract
            else ". "))
        + f"{len(ax_tainted)} theorem(s) are NOT established ({ax_tainted}) — still `sorry` or "
        f"dependent on a non-standard axiom. Reconciliation: critical_discrepancies={rc_critical}, "
        f"total_refinement_obligations={rc_obligations_n}."
        f"\n\n{report_files}" + resume_note,
        deps, "REPORT",
    )

    _REPORT_SECTIONS = [
        "report/01_overview.md", "report/02_translation.md",
        "report/03_abstract_spec.md", "report/04_implementation_spec.md",
        "report/05_spec_judge.md", "report/06_reconciliation.md",
        "report/07_proofs.md", "report/08_summary.md",
    ]
    parts = [
        content for sf in _REPORT_SECTIONS
        for content in [tools.read_out(deps, sf)]
        if not content.startswith("ERROR:")
    ]
    if parts:
        report_text = "\n\n".join(parts)
        log.info("Concatenating %d/%d report sections into VERIFICATION_REPORT.md",
                 len(parts), len(_REPORT_SECTIONS))
    else:
        report_text = report_result.output if report_result else ""
        if report_text:
            log.warning("No report/ sections found — falling back to agent text output")
        else:
            log.warning("REPORT stage produced no sections and no text output")

    if report_text:
        try:
            tools.write_out(deps, "VERIFICATION_REPORT.md", report_text)
            # glob="." stages the report/ sections too (only report files are pending here).
            tools.commit(deps.container_id, "stage/report: final pipeline report", glob=".")
            log.info("Report written and committed (%d chars)", len(report_text))
        except Exception as e:
            log.warning("Could not write report: %s", e)

    _checkpoint(deps)
    return report_text or "(no report generated)"


def _write_abort_notes(deps: AgentDeps, reason: str) -> None:
    """On a hard abort, drop an 'incomplete verification' notes artefact (committed, so it
    is pulled with the rest of the output) summarising what was accomplished and what could
    not be — so an aborted run leaves something actionable rather than empty output."""
    p = deps.progress
    stages = [
        ("abstract_informal_spec", "DOC-INFER — abstract informal spec"),
        ("abstract_formal_spec",   "DOC-FORMALISE — abstract Lean stubs"),
        ("aeneas",                 "TRANSLATE — Aeneas translation"),
        ("informal_spec",          "INFER — implementation informal spec"),
        ("formal_spec",            "FORMALISE — implementation spec assembled"),
        ("verdict",                "SPEC-JUDGE — statements judged"),
        ("reconciliation",         "RECONCILE — abstract vs impl"),
        ("proofs_done",            "PROVE — proofs attempted"),
    ]
    done = [f"- {label}" for key, label in stages if key in p] or ["- (nothing yet)"]
    todo = [f"- {label}" for key, label in stages if key not in p] or ["- (all reached)"]
    lines = [
        "# Verification incomplete",
        "",
        f"The pipeline aborted before finishing. **Reason:** {reason}",
        "",
        "## Completed", *done, "",
        "## Not reached", *todo, "",
    ]
    if "aeneas" in p:
        holes = p["aeneas"].get("holes", [])
        lines += ["## Translation",
                  f"- Untranslated Aeneas holes: {', '.join(holes) if holes else 'none'}", ""]
    build = p.get("lean_build", {})
    if build and not build.get("success"):
        lines += ["## Last build errors (the implementation spec did not compile)",
                  "```", (build.get("stderr", "") or "")[:3000], "```", ""]
    defects = (p.get("verdict") or {}).get("defects", [])
    if defects:
        lines += ["## Outstanding spec-judge defects",
                  *(f"- {d['theorem']} [{d['kind']}]: {d['detail']}" for d in defects), ""]
    lines += ["## What this means",
              "No end-to-end verification was produced. Review the artefacts (specs/, lean/) "
              "and the reason above, address the blocker, then re-run.", ""]
    try:
        tools.write_out(deps, "VERIFICATION_INCOMPLETE.md", "\n".join(lines))
        tools.commit(deps.container_id, "chore: incomplete-verification notes", glob=".")
        log.info("Wrote VERIFICATION_INCOMPLETE.md (abort notes)")
    except Exception as e:
        log.warning("Could not write abort notes: %s", e)


# ── main entry point ──────────────────────────────────────────────────────────

async def run_session(deps: AgentDeps) -> str:
    """Drive the pipeline stage by stage and return a final summary string."""
    resuming = bool(deps.progress)

    if resuming:
        log.info("Resuming session — completed stages: %s", sorted(deps.progress.keys()))

    resume_note = (
        "\n\nSESSION RESUMED — the Docker container is fresh but all previously "
        "generated artefacts have been restored. Continue from where you left off."
        if resuming else ""
    )

    try:
        resume_note = await _run_doc_stages(deps, resume_note)
        resume_note = await _run_translate_stages(deps, resume_note)

        resume_note = await SpecPhase(deps, resume_note).run()
        await _run_reconcile_phase(deps)
        await ProvePhase(deps).run()

        result = await _run_report(deps, resume_note)

        # Final tidy: any stage may leave stray spec files; prune once at the end.
        tools.prune_stray_specs(deps, deps.progress.get("aeneas", {}).get("lean_path", ""))
        tools.commit(deps.container_id, "chore: prune stray spec files", glob=".")

        u = telemetry.session.as_dict()
        log.info(
            "Pipeline complete — session usage: in=%d out=%d cache_read=%d cache_write=%d total=%d/%s",
            u["input_tokens"], u["output_tokens"],
            u["cache_read_tokens"], u["cache_write_tokens"],
            u["total_tokens"], telemetry.budget or "∞",
        )
        return result

    except _PipelineAborted as e:
        log.error("Pipeline aborted: %s", e)
        _write_abort_notes(deps, str(e))
        return f"Pipeline aborted: {e}"
