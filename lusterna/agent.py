"""Pipeline orchestration: sequencing, loops, per-stage context, and tool wiring.

Stage agents are declared in stages.py; this module imports them, attaches their
tools, and drives them. Each stage runs independently with no shared message
history — stages communicate via the filesystem and deps.progress, not via
conversation context.
"""
import logging
from typing import Any, Callable

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from . import checkpoint, git_ops, telemetry, tools
from .schemas import (
    AbstractInformalSpec, AbstractFormalSpec,
    InformalSpec, FormalSpec, JudgeVerdict, ReconciliationReport,
)
from .stages import (
    doc_infer as _doc_infer, doc_formalise as _doc_formalise, explore as _explore,
    infer as _infer, formalise as _formalise,
    judge as _judge, reconcile as _reconcile, prove as _prove, report as _report,
)
from .state import AgentDeps
from . import config

log = logging.getLogger(__name__)


def check_lean(ctx: RunContext[AgentDeps], lean_file: str) -> dict:
    """Run `lake build` in the Lean project and return {success, stderr}.

    The result is stored in progress['lean_build'] and a checkpoint is saved.
    Always call this after writing or modifying any Lean file.
    """
    result = tools.check_lean(ctx.deps, lean_file)
    ctx.deps.progress["lean_build"] = result
    ctx.deps.progress["build_seq"] = ctx.deps.progress.get("build_seq", 0) + 1
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
            "git_head": git_ops.head_sha(deps.container_id),
        },
    )


class _PipelineAborted(Exception):
    pass


_HARD_CAP = 10    # max spec-judge rounds
_NO_PROGRESS = 3  # PROVE: give up after this many successful builds with no new sorry minimum


def _impl_spec(deps: AgentDeps) -> str:
    """Canonical implementation-spec path — the single file FORMALISE fills and PROVE
    proves. Placed under the Aeneas lib root (lean/<Crate>/) so the existing lakefile
    builds it with no lakefile changes. Deterministic, never agent-chosen."""
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    if not lean_path:
        return ""
    stem = lean_path.rsplit("/", 1)[-1].removesuffix(".lean")
    return f"lean/{stem}/Spec.lean"


def _assemble_impl_spec(deps: AgentDeps, fs: FormalSpec) -> None:
    """Write the implementation spec from a structured FormalSpec: the preamble followed by
    one `theorem <name> <signature> := by sorry` per stub. Proofs are added here as `sorry`
    — never by the model — so FORMALISE cannot smuggle in a proof (or a hanging tactic).
    The preamble is passed through stub_proofs as a safety net for any stray theorem."""
    path = _impl_spec(deps)
    stem = path.split("/")[1]
    preamble = tools.stub_proofs(fs.preamble.rstrip())
    header = "".join(
        f"import {m}\n" for m in ("Aeneas", stem) if f"import {m}" not in preamble
    )
    body = "\n\n".join(
        f"theorem {t.name} {t.signature.split(':=')[0].strip()} := by sorry"
        for t in fs.theorems
    )
    tools.write_out(deps, path, f"{header}{preamble}\n\n{body}\n")
    log.info("Assembled impl spec: %d theorem stub(s) at %s", len(fs.theorems), path)


def _build(deps: AgentDeps) -> dict:
    """Run `lake build` on the impl spec and record it as PROVE's progress metric does."""
    result = tools.check_lean(deps, _impl_spec(deps))
    deps.progress["lean_build"] = result
    deps.progress["build_seq"] = deps.progress.get("build_seq", 0) + 1
    _checkpoint(deps)
    return result


def _sorry_count(deps: AgentDeps) -> int:
    """Remaining `sorry` in the implementation spec — PROVE's progress metric.
    Returns -1 if the file can't be read, so the caller never mistakes it for done."""
    content = tools.read_out(deps, _impl_spec(deps))
    return -1 if content.startswith("ERROR:") else content.count("sorry")


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
        roots = tools.referenced_defs(spec, translation)
        defs = tools.call_closure(translation, roots)
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
                     stop_check: Callable[[AgentDeps], bool] | None = None) -> Any:
    """Run one stage agent. Each stage starts with no prior history.

    Stages communicate via the filesystem and deps.progress, not via conversation
    context — so no history is passed in or accumulated across stages. When stop_check
    is given it is polled between nodes; returning True ends the run early.
    Re-raises UnexpectedModelBehavior; judge stages catch it locally.
    """
    log.info("─── Stage: %s ───", label)
    telemetry.stage.reset()
    deps.message_history = []
    full_prompt = _pipeline_briefing(deps) + prompt
    from pydantic_ai.usage import UsageLimits
    async with agent.iter(
        full_prompt, deps=deps,
        usage_limits=UsageLimits(request_limit=config.REQUEST_LIMIT),
    ) as run:
        async for _node in run:
            if stop_check and stop_check(deps):
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
            git_ops.commit(deps.container_id, "feat(spec): abstract formal specification", glob="specs/")
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
        result = tools.run_aeneas(deps, entry)
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
            git_ops.commit(deps.container_id, "feat(spec): informal specification", glob="specs/")
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


async def _run_spec_phase(deps: AgentDeps, resume_note: str) -> str:
    """Drive FORMALISE (structured, statements only) → assemble → build → SPEC-JUDGE in one
    loop. Each round FORMALISE (re)states the properties as a FormalSpec; the orchestrator
    assembles them as `:= by sorry` stubs (so no proof can be smuggled in) and builds — the
    build here only validates that the STATEMENTS typecheck. A failed statement build and a
    spec-judge defect list are both fed back as revision feedback. Ends when the spec builds
    AND has no defects, the defects repeat (no progress), or the round cap is hit."""
    if "verdict" in deps.progress:
        if "footprint" not in deps.progress:
            _footprint(deps)
        return resume_note

    impl_spec = _impl_spec(deps)
    base_inputs = _formalise_inputs(deps)
    prev_defects: frozenset | None = None
    feedback = ""            # revision feedback carried between rounds (build errors / defects)
    attempt = 0
    while attempt < _HARD_CAP:
        # 1) FORMALISE — (re)state the properties as structured, proof-free stubs.
        if attempt == 0:
            instruction = (
                "FORMALISE: return a FormalSpec (preamble + statement-only theorems) "
                "capturing the properties that matter — the behaviour the design document "
                "describes, as realised by the crate. You write no proofs."
            )
        else:
            cur = tools.read_out(deps, impl_spec)
            instruction = (
                f"FORMALISE revision (round {attempt + 1}). Return an updated FormalSpec. "
                f"{feedback}\n\n### current assembled spec\n{cur}"
            )
        fr = await _run_stage(_formalise, f"{instruction}\n\n{base_inputs}" + resume_note,
                              deps, f"FORMALISE (round {attempt + 1})")
        resume_note = ""
        if not (fr and fr.output):
            log.warning("FORMALISE produced no output — stopping spec loop")
            break
        _assemble_impl_spec(deps, fr.output)
        deps.progress["formal_spec"] = True

        # 2) Build — validate the STATEMENTS typecheck (bodies are all `sorry`, so fast).
        if not _build(deps).get("success"):
            log.info("FORMALISE round %d: statements do not compile — feeding errors back",
                     attempt + 1)
            feedback = ("The assembled spec did NOT compile. Fix the STATEMENTS and/or the "
                        "preamble so it typechecks (you still write no proofs — bodies are "
                        f"`sorry`). Build errors:\n{deps.progress['lean_build'].get('stderr','')[:2000]}")
            attempt += 1
            continue

        # 3) SPEC-JUDGE — judge the statements against the crate + informal spec.
        judge_files = f"### Aeneas-translated crate\n{_translation_text(deps)}\n\n"
        for path in ("specs/informal_spec.json", impl_spec):
            content = tools.read_out(deps, path)
            if not content.startswith("ERROR:"):
                judge_files += f"### {path}\n{content}\n\n"
        try:
            sj = await _run_stage(
                _judge,
                "SPEC-JUDGE: list every defect in the implementation spec's theorem "
                "statements (ignore sorry proofs); return an empty list if it is sound."
                f"\n\n{judge_files}",
                deps, f"SPEC-JUDGE (round {attempt + 1})",
            )
        except UnexpectedModelBehavior as e:
            log.warning("Spec judge failed after retries: %s — stopping spec loop", e)
            break
        if not (sj and sj.output):
            log.warning("Spec judge produced no output — stopping spec loop")
            break

        sv: JudgeVerdict = sj.output
        deps.progress["verdict"] = sv.model_dump()
        _checkpoint(deps)
        if not sv.defects:
            log.info("Spec-judge round %d: no defects — spec approved", attempt + 1)
            break
        log.info("Spec-judge round %d: %d defect(s): %s", attempt + 1, len(sv.defects),
                 ", ".join(f"{d.theorem}[{d.kind}]" for d in sv.defects))

        defect_sig = frozenset((d.theorem, d.kind) for d in sv.defects)
        if defect_sig == prev_defects:
            log.warning("Spec-judge: same defects as last round — no progress, stopping")
            break
        prev_defects = defect_sig
        defect_lines = "\n".join(
            f"  - {d.theorem} [{d.kind}]: {d.detail} → fix: {d.fix}" for d in sv.defects
        )
        feedback = (
            f"The spec-judge found {len(sv.defects)} defect(s) in the STATEMENTS. Revise to "
            f"fix EVERY one below — do not skip or deem any redundant. If a fix asks for a "
            f"theorem, add it as a stub (state it explicitly even if another theorem entails "
            f"it). Change nothing else.\n{defect_lines}"
        )
        attempt += 1
    else:
        log.warning("Spec loop hit hard cap of %d rounds", _HARD_CAP)

    _footprint(deps)   # which Aeneas holes (if any) actually touch the stated properties
    return resume_note


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
        git_ops.commit(
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


async def _run_prove_phase(deps: AgentDeps) -> None:
    """Run PROVE, stopped by the orchestrator on an objective metric.

    After each successful build the remaining `sorry` count in the spec is checked:
    the stage ends when it reaches 0, shows no new minimum for _NO_PROGRESS builds, or
    MAX_PROVE_ROUNDS builds elapse. Proof status is read from the file (proved = no
    `sorry`); there is no separate proof-judge.
    """
    if "proofs_done" in deps.progress:
        log.info("PROVE already complete — skipping proof stage")
        return

    rc = deps.progress.get("reconciliation", {})
    critical = [d for d in rc.get("discrepancies", []) if d.get("severity") == "critical"]
    rc_obligations = rc.get("refinement_obligations", [])
    prove_note = ""
    if rc_obligations:
        prove_note = (
            f"\n\nRECONCILIATION NOTE: {len(rc_obligations)} refinement obligation(s) "
            "were generated to bridge the impl spec to the abstract spec. "
            "Their sorry stubs are in specs/reconciliation.json. "
            "You may add them to the spec file and attempt to prove them."
        )
    if critical:
        critical_lines = "\n".join(
            f"  [{d['kind']}] {d['description'][:100]}" for d in critical
        )
        prove_note += (
            f"\n\nCRITICAL ({len(critical)} — do NOT paper over):\n"
            f"{critical_lines}\n"
            "These indicate a potential bug in the implementation or a mis-stated theorem. "
            "Leave the corresponding obligations unproved and note them clearly."
        )

    # Objective stop: end PROVE when no sorry remain, no new minimum sorry-count for
    # _NO_PROGRESS successful builds, or MAX_PROVE_ROUNDS builds elapse. Evaluated once
    # per new successful build (tracked via build_seq), never on intermediate nodes.
    track = {"seq": deps.progress.get("build_seq", 0), "best": None, "flat": 0, "rounds": 0}

    def _prove_stop(deps: AgentDeps) -> bool:
        seq = deps.progress.get("build_seq", 0)
        if seq == track["seq"]:
            return False
        track["seq"] = seq
        if not deps.progress.get("lean_build", {}).get("success"):
            return False
        track["rounds"] += 1
        n = _sorry_count(deps)
        if n < 0:
            log.warning("PROVE build %d: impl spec unreadable — deferring to round cap",
                        track["rounds"])
            return track["rounds"] >= config.MAX_PROVE_ROUNDS
        if track["best"] is None or n < track["best"]:
            track["best"], track["flat"] = n, 0
        else:
            track["flat"] += 1
        log.info("PROVE build %d: sorry=%d (best=%d, no-progress=%d)",
                 track["rounds"], n, track["best"], track["flat"])
        return (n == 0 or track["flat"] >= _NO_PROGRESS
                or track["rounds"] >= config.MAX_PROVE_ROUNDS)

    try:
        await _run_stage(
            _prove,
            "Proceed to PROVE. Attempt to fill in proofs "
            "for all sorry theorems in the spec. Do not alter any statement. "
            "Commit the result when done." + prove_note,
            deps, "PROVE", stop_check=_prove_stop,
        )
    except UnexpectedModelBehavior as e:
        log.warning("PROVE stage failed after retries: %s", e)
    _checkpoint(deps)
    # The orchestrator may force-stop PROVE before the agent commits — commit here so
    # the proven state always lands in git history.
    git_ops.commit(deps.container_id, "stage/prove: proof attempts", glob="lean/")

    if not deps.progress.get("lean_build", {}).get("success"):
        raise _PipelineAborted("PROVE produced no successful lake build")

    deps.progress["proofs_done"] = True
    _footprint(deps)   # cheap pre-oracle estimate — proofs may reference defs the stubs did not
    # Authoritative soundness verdict: ask Lean which theorems depend on no `sorryAx`.
    ax = tools.check_axioms(deps, _impl_spec(deps))
    deps.progress["axioms"] = {"clean": ax["clean"], "tainted": ax["tainted"]}
    _checkpoint(deps)
    log.info("PROVE complete — %d sorry remaining; genuinely established (no sorryAx): "
             "%d/%d theorem(s); clean=%s tainted=%s",
             max(_sorry_count(deps), 0), len(ax["clean"]),
             len(ax["clean"]) + len(ax["tainted"]), ax["clean"], ax["tainted"])


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
        f"(Lean `#print axioms`): {len(ax_clean)} theorem(s) genuinely established with no "
        f"sorryAx ({ax_clean}); {len(ax_tainted)} still depend on sorry ({ax_tainted}) — "
        f"a proof that looks complete but touches an untranslated hole is caught here. "
        f"Reconciliation: critical_discrepancies={rc_critical}, "
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
            git_ops.commit(deps.container_id, "stage/report: final pipeline report", glob=".")
            log.info("Report written and committed (%d chars)", len(report_text))
        except Exception as e:
            log.warning("Could not write report: %s", e)

    _checkpoint(deps)
    return report_text or "(no report generated)"


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

        resume_note = await _run_spec_phase(deps, resume_note)
        await _run_reconcile_phase(deps)
        await _run_prove_phase(deps)

        result = await _run_report(deps, resume_note)

        # Final tidy: any stage may leave stray spec files; prune once at the end.
        tools.prune_stray_specs(deps, deps.progress.get("aeneas", {}).get("lean_path", ""))
        git_ops.commit(deps.container_id, "chore: prune stray spec files", glob=".")

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
        return f"Pipeline aborted: {e}"
