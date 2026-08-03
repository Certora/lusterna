"""The pipeline spine (spawn model): a deterministic sequencer that runs each stage as a headless
Claude Code session (runner.run_cc_stage) and applies the TRUSTED, agent-inaccessible mechanical
gates to the files the session produces. The agents own the labor; this module owns the trust.

Stages: EXPLORE → INFER → TRANSLATE (+ TRANSLATE-JUDGE) → FORMALISE (+ SPEC-JUDGE) → PROVE → REPORT.
Each stage's deliverable is FILES under /workspace/out; the harness reads them and gates:
  • the compile gate — `lake build` (lean.build / lean.translation_compiles),
  • the soundness gate — `#print axioms` (lean.check_axioms), the authoritative established verdict,
  • no proof smuggling — lean.stub_proofs re-stubs FORMALISE's theorem bodies before acceptance,
  • the audit trail — the pristine-baseline git diff (tools.repo_diff).
These never move and are never delegated. Everything else (iteration, judging) is the CC session's
job; the harness re-invokes a stage (resume) with the gate's feedback until it passes or STALL_ROUNDS
rounds fail to.
"""
import json
import logging
import re

from . import briefings, checkpoint, config, container, lean, tools
from .runner import run_cc_stage, StageFailed
from .schemas import AgentDeps

log = logging.getLogger(__name__)


class _PipelineAborted(Exception):
    """Raised to stop the pipeline gracefully (caught in run_session): the run cannot proceed but
    should degrade to a partial report + keep-alive, never crash with a traceback."""


# ── shared CC-stage plumbing ────────────────────────────────────────────────────

def _cc_common() -> dict:
    """Model / effort / per-stage runaway budget shared by every spawned stage session."""
    return dict(model=config.CC_MODEL, effort=config.EFFORT or None,
                max_budget_usd=config.CC_STAGE_BUDGET_USD)


def _cc_gate_loop(deps: AgentDeps, *, stage: str, briefing: str, base_prompt: str, check) -> None:
    """Run `stage` as a CC session, apply its TRUSTED gate (`check(deps) -> (ok, feedback)`, incl.
    any judge sub-session), and RESUME the session with the feedback until it passes.

    PROGRESS-AWARE stall (not a fixed round cap): a round whose gate feedback CHANGES is making
    progress (a defect fixed, a new one surfaced) and the loop continues; only STALL_ROUNDS
    CONSECUTIVE rounds with the SAME failure (feedback unchanged modulo volatile line/col numbers)
    count as stuck and abort. So a genuinely-improving FORMALISE/TRANSLATE loop is never cut off
    mid-progress; a stuck/oscillating one still stops. No hard round ceiling — the per-stage
    --max-budget-usd bounds each round's cost. Mirrors the TRANSLATE/PROVE best-tracked stalls.
    """
    prompt, feedback, prev_key, stale, rnd = base_prompt, "", None, 0, 0
    while True:
        rnd += 1
        sid = deps.progress.get("cc_sessions", {}).get(stage)   # resume once a round has run
        try:
            run_cc_stage(deps, stage=stage, prompt=prompt, briefing=(None if sid else briefing),
                         resume_sid=sid, **_cc_common())
        except StageFailed as e:
            log.warning("%s round %d: session produced no result (%s) — checking artefacts anyway",
                        stage, rnd, e)
        ok, feedback = check(deps)
        checkpoint.snapshot(deps)
        if ok:
            return
        key = re.sub(r"\d+", "#", feedback or "")   # normalise volatile numbers (line:col, counts)
        stale = stale + 1 if key == prev_key else 1
        prev_key = key
        log.info("%s round %d — gate not satisfied (same-failure streak %d/%d): %s",
                 stage, rnd, stale, config.STALL_ROUNDS, (feedback or "")[:200])
        if stale >= config.STALL_ROUNDS:
            raise _PipelineAborted(
                f"{stage} stalled — the same gate failure recurred {config.STALL_ROUNDS} rounds "
                f"with no progress: {(feedback or '')[:300]}")
        prompt = ("Your previous attempt did NOT pass the harness gate. Fix EXACTLY the following, "
                  "then finish:\n" + feedback)


def _run_judge(deps: AgentDeps, *, stage: str, briefing: str, prompt: str, verdict_rel: str) -> dict:
    """Spawn an INDEPENDENT CC judge session (fresh context — structural independence from the stage
    it judges), then read + parse its verdict file. A soft gate: if the judge writes no parseable
    verdict, treat it as APPROVE (never block the pipeline on the semantic screen; the hard
    mechanical gate already passed). Returns {"defects": [...]}."""
    container.exec_in(deps.container_id, ["rm", "-f", f"{container.OUT_IN}/{verdict_rel}"])
    try:
        run_cc_stage(deps, stage=stage, prompt=prompt, briefing=briefing, **_cc_common())
    except StageFailed as e:
        log.warning("%s errored (%s) — approving on the hard gate alone", stage, e)
        return {"defects": []}
    raw = tools.read_out(deps, verdict_rel)
    if raw.startswith("ERROR:"):
        log.warning("%s wrote no %s — approving on the hard gate alone", stage, verdict_rel)
        return {"defects": []}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("%s verdict is not valid JSON — approving on the hard gate alone", stage)
        return {"defects": []}


def _format_defects(defects: list) -> str:
    """One line per defect for the resume-feedback prompt."""
    out = []
    for d in defects:
        who = d.get("theorem") or d.get("kind", "?")
        out.append(f"  - {who} [{d.get('kind', '?')}]: {d.get('detail', '')} "
                   f"→ fix: {d.get('fix', '')}")
    return "\n".join(out)


# ── EXPLORE ─────────────────────────────────────────────────────────────────────

def _stage_explore(deps: AgentDeps) -> None:
    if "explore" in deps.progress:
        return
    hint = (f"\n\nDesign focus hint (optional, non-authoritative — which code matters):\n"
            f"{deps.design_doc.rstrip()}" if deps.design_doc.strip() else "")
    run_cc_stage(
        deps, stage="EXPLORE", briefing=briefings.EXPLORE,
        prompt=("Orient to the code at /workspace/repo and run the toolchain reality-check, then "
                "write /workspace/out/explore/assessment.md and the machine-readable "
                "/workspace/out/explore/handoff.json per your briefing. Stop once handoff.json "
                "exists and is valid." + hint),
        **_cc_common(),
    )
    handoff = tools.read_out(deps, "explore/handoff.json")
    if handoff.startswith("ERROR:"):
        raise _PipelineAborted("EXPLORE produced no /workspace/out/explore/handoff.json")
    try:
        deps.progress["explore"] = json.loads(handoff)
    except json.JSONDecodeError as e:
        raise _PipelineAborted(f"EXPLORE handoff.json is not valid JSON: {e}")
    # Fold any build-env prep into the pristine baseline so it is not mistaken for a TRANSLATE
    # source modification in the accountability diff.
    container.commit_build_prep(deps.container_id)
    checkpoint.snapshot(deps)


# ── INFER ───────────────────────────────────────────────────────────────────────

def _stage_infer(deps: AgentDeps) -> None:
    if "informal_spec" in deps.progress:
        return
    hint = (f"\n\nDesign focus hint (optional, non-authoritative):\n{deps.design_doc.rstrip()}"
            if deps.design_doc.strip() else "")

    def check(deps: AgentDeps):
        raw = tools.read_out(deps, "specs/informal_spec.json")
        if raw.startswith("ERROR:"):
            return False, "specs/informal_spec.json is missing — write it per the briefing."
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError as e:
            return False, f"specs/informal_spec.json is not valid JSON: {e}"
        if "target_patterns" not in spec:
            return False, "specs/informal_spec.json is missing the required key `target_patterns`."
        deps.progress["informal_spec"] = spec
        deps.progress["target_patterns"] = list(spec.get("target_patterns") or [])
        return True, ""

    _cc_gate_loop(
        deps, stage="INFER", briefing=briefings.INFER,
        base_prompt=("Proceed to INFER. Read the pristine Rust at /workspace/repo and "
                     "/workspace/out/explore/handoff.json, then write "
                     "/workspace/out/specs/informal_spec.json per your briefing." + hint),
        check=check,
    )
    tools.commit(deps.container_id, "feat(spec): informal specification (pre-translate)")
    log.info("INFER target patterns: %s", deps.progress.get("target_patterns") or "(whole crate)")


# ── TRANSLATE (+ TRANSLATE-JUDGE) ─────────────────────────────────────────────────

def _translate_facts(deps: AgentDeps) -> dict:
    """The harness's mechanical TRANSLATE gate, kept to the bare minimum: Lean was produced, the
    layout is clean (ONE top-level module — no -split-files), and it COMPILES. Whether the target is
    genuinely translated (a real `def`, not mocked) is the TRANSLATE-JUDGE's semantic call, and a
    hollow target is caught downstream by `#print axioms` (its theorems come out tainted) — so the
    harness no longer re-derives that from fragile target-name matching."""
    lean.setup_lake(deps)
    info = lean.analyze_translation(deps, do_commit=False)
    facts = {"success": info["success"], "lean_files": info["lean_files"],
             "lean_path": info["lean_path"], "holes": info["holes"],
             "compiles": False, "build_errors": "", "polluted": []}
    top_level = [f for f in info["lean_files"]
                 if f.startswith("lean/") and "/" not in f[len("lean/"):]]
    facts["polluted"] = sorted(top_level) if len(top_level) > 1 else []
    if not info["success"]:
        return facts
    build = lean.translation_compiles(deps, info["lean_path"])
    facts["compiles"] = bool(build.get("success"))
    facts["build_errors"] = (build.get("stderr", "") or "")[:2000]
    return facts


def _stage_translate(deps: AgentDeps) -> None:
    if "aeneas" in deps.progress:
        return
    targets = deps.progress.get("target_patterns") or []
    entry = deps.progress.get("explore", {}).get("entry_file", "src/lib.rs")

    def check(deps: AgentDeps):
        facts = _translate_facts(deps)
        if not facts["success"]:
            return False, "no Lean was produced in /workspace/out/lean — run charon then aeneas"
        if facts["polluted"]:
            return False, (f"POLLUTED tree — multiple top-level modules {facts['polluted']}; "
                           f"`rm -rf /workspace/out/lean/*` and re-run aeneas WITHOUT -split-files")
        if not facts["compiles"]:
            return False, "the translation does not compile:\n" + facts["build_errors"]
        # Mechanical hard gate passed (it compiles). Whether the TARGET is genuinely translated (a
        # real def, not opaqued/holed) and behaviour-preserving is the TRANSLATE-JUDGE's semantic
        # call — it inspects the translation itself — with `#print axioms` the final backstop.
        judge_facts = {
            "compiles": True,
            "emitted_axioms": lean.external_axioms(lean.translation_text(deps, facts["lean_files"])),
            "generated_lean_files": facts["lean_files"],
            "source_files_changed": tools.repo_changed_files(deps.container_id),
            "source_git_diff": tools.repo_diff(deps.container_id),
        }
        tools.write_out(deps, "translate/facts.json", json.dumps(judge_facts, indent=2))
        verdict = _run_judge(
            deps, stage="TRANSLATE-JUDGE", briefing=briefings.TRANSLATE_JUDGE,
            prompt=(f"Judge the translation in /workspace/out/lean against the target source in "
                    f"/workspace/repo. The target functions are: {targets or '(whole crate)'}. Read "
                    f"the facts at /workspace/out/translate/facts.json and the accountability at "
                    f"/workspace/out/translate/accountability.md, then write your verdict to "
                    f"/workspace/out/translate/verdict.json per your briefing."),
            verdict_rel="translate/verdict.json")
        defects = verdict.get("defects", [])
        if defects:
            return False, "TRANSLATE-JUDGE found defects:\n" + _format_defects(defects)
        deps.progress["aeneas"] = {"lean_path": facts["lean_path"],
                                   "lean_files": facts["lean_files"], "holes": facts["holes"]}
        return True, ""

    _cc_gate_loop(
        deps, stage="TRANSLATE", briefing=briefings.TRANSLATE,
        base_prompt=(f"Proceed to TRANSLATE. Target patterns (each MUST become a real `def`, never "
                     f"opaqued): {targets or '(whole crate)'}. Suggested entry file: {entry}. Read "
                     f"/workspace/out/specs/informal_spec.json and /workspace/out/explore/handoff.json, "
                     f"then drive Charon + Aeneas into /workspace/out/lean per your briefing."),
        check=check,
    )
    tools.commit(deps.container_id, "feat(translate): aeneas translation of the target")


# ── FORMALISE (+ SPEC-JUDGE) ──────────────────────────────────────────────────────

def _stage_formalise(deps: AgentDeps) -> None:
    if "spec_ok" in deps.progress:
        return
    impl = lean.impl_spec(deps)

    def check(deps: AgentDeps):
        raw = tools.read_out(deps, impl)
        if raw.startswith("ERROR:"):
            return False, f"the spec {impl} is missing — write the statement-only spec there."
        # TRUSTED no-smuggle gate: force every theorem body to `:= by sorry` before acceptance,
        # so FORMALISE cannot sneak a proof past PROVE. Then the compile gate on the statements.
        tools.write_out(deps, impl, lean.stub_proofs(raw))
        deps.progress["formal_spec"] = True
        if not lean.build(deps).get("success"):
            err = deps.progress.get("lean_build", {}).get("stderr", "")
            return False, "the theorem STATEMENTS do not compile (you still write NO proofs):\n" + err[-1500:]
        verdict = _run_judge(
            deps, stage="SPEC-JUDGE", briefing=briefings.SPEC_JUDGE,
            prompt=(f"Judge the theorem STATEMENTS in /workspace/out/{impl} against the translation "
                    f"in /workspace/out/lean and /workspace/out/specs/informal_spec.json, then write "
                    f"your verdict to /workspace/out/spec/verdict.json per your briefing."),
            verdict_rel="spec/verdict.json")
        deps.progress["verdict"] = verdict
        defects = verdict.get("defects", [])
        if defects:
            return False, "SPEC-JUDGE found defects in the statements:\n" + _format_defects(defects)
        deps.progress["spec_ok"] = True
        return True, ""

    _cc_gate_loop(
        deps, stage="FORMALISE", briefing=briefings.FORMALISE,
        base_prompt=(f"Proceed to FORMALISE. Read /workspace/out/specs/informal_spec.json and the "
                     f"translation under /workspace/out/lean, then write the statement-only spec "
                     f"(theorem bodies `:= by sorry`) to /workspace/out/{impl} per your briefing. "
                     f"Ensure it compiles with `lake env lean`."),
        check=check,
    )
    tools.commit(deps.container_id, "feat(spec): implementation spec (statements only)")


# ── PROVE ─────────────────────────────────────────────────────────────────────────

def _prove_feedback(deps: AgentDeps, ax: dict) -> str:
    """Name the frontier for the next PROVE round: already-established (keep verbatim), still-open
    `sorry`, and compiles-but-tainted (a non-standard axiom, usually native_decide)."""
    spec = tools.read_out(deps, lean.impl_spec(deps))
    sorry_names = lean.sorry_bodied_theorems(spec)
    open_sorry = [n for n in ax["tainted"] if n in sorry_names]
    tainted_non_sorry = [n for n in ax["tainted"] if n not in sorry_names]
    parts = [
        "Continue PROVE (fresh attempt — try tactics/lemmas you have NOT tried yet).",
        f"Already ESTABLISHED — keep these proofs EXACTLY, do not touch them: {ax['clean'] or '(none yet)'}.",
    ]
    if open_sorry:
        parts.append(f"Still OPEN (`:= by sorry`) — focus here: {open_sorry}.")
    if tainted_non_sorry:
        parts.append(f"These COMPILE but are NOT established — the proof rests on a non-standard "
                     f"axiom (almost always native_decide/decide on a recursive eval): "
                     f"{tainted_non_sorry}. Replace with a REASONING proof or revert to `:= by sorry`.")
    return "\n".join(parts)


def _record_axioms(deps: AgentDeps) -> None:
    """`#print axioms` (standard-axioms-only) + partition established theorems into those that VERIFY
    THE IMPLEMENTATION (reference a real Aeneas def) vs abstract helper lemmas.

    The harness does NOT re-derive a faithfulness tier: rung-3 modeling is a sanctioned capability,
    screened once (semantically) by the TRANSLATE-JUDGE and disclosed by the agent in
    translate/accountability.md — that trail is the record, not a redundant harness verdict."""
    impl = lean.impl_spec(deps)
    ax = lean.check_axioms(deps, impl)
    translation = lean.translation_text(deps)
    spec_text = tools.read_out(deps, impl)
    impl_verified, abstract_only = [], []
    for name in ax["clean"]:
        stmt = lean.theorem_statement(spec_text, name)
        (impl_verified if stmt and lean.referenced_defs(stmt, translation)
         else abstract_only).append(name)
    deps.progress["axioms"] = {"clean": ax["clean"], "tainted": ax["tainted"],
                               "impl_verified": impl_verified, "abstract_only": abstract_only}
    checkpoint.snapshot(deps)
    if ax["clean"] and not impl_verified:
        log.warning("PROVE: ⚠ CRITICAL — %d theorem(s) established but NONE reference the "
                    "implementation; 0 properties of the code are verified (abstract lemmas only: %s)",
                    len(ax["clean"]), abstract_only)
    log.info("PROVE complete — %d sorry remaining; established: %d/%d — %d verify the implementation "
             "%s, %d abstract-only %s; tainted=%s",
             max(lean.sorry_count(deps), 0), len(ax["clean"]),
             len(ax["clean"]) + len(ax["tainted"]), len(impl_verified), impl_verified,
             len(abstract_only), abstract_only, ax["tainted"])


def _stage_prove(deps: AgentDeps) -> None:
    """Best-tracked stall loop over a RESUMED PROVE session, scored by the axiom-clean established
    count. A round that fails to beat the best is discarded (restore best), so the count is monotone
    and one bad round can't lose proofs. Stops when nothing tainted remains or after STALL_ROUNDS
    with no gain. The `#print axioms` gate and the compile gate are the trusted arbiters."""
    if "proofs_done" in deps.progress:
        return
    impl = lean.impl_spec(deps)
    if not lean.build(deps).get("success"):
        raise _PipelineAborted("PROVE not started — the implementation spec does not compile")
    best_spec = tools.read_out(deps, impl)
    best_established, stale, rnd = -1, 0, 0
    prompt = (f"Proceed to PROVE. Fill in proofs for as many `sorry` theorems in /workspace/out/{impl} "
              f"as you can, WITHOUT changing any statement. Work one theorem at a time against "
              f"`lake build`, keep the file compiling, then commit. Follow your briefing's strict rules.")
    while True:
        rnd += 1
        sid = deps.progress.get("cc_sessions", {}).get("PROVE")
        stop = False
        try:
            run_cc_stage(deps, stage="PROVE", prompt=prompt,
                         briefing=(None if sid else briefings.PROVE), resume_sid=sid, **_cc_common())
        except StageFailed as e:
            log.warning("PROVE round %d: session produced no result (%s) — scoring on-disk", rnd, e)
        checkpoint.snapshot(deps)
        if not lean.build(deps).get("success"):
            log.warning("PROVE round %d left a non-compiling spec — restoring best-so-far", rnd)
            tools.write_out(deps, impl, best_spec)
            lean.build(deps)
        ax = lean.check_axioms(deps, impl)
        established = len(ax["clean"])
        total = established + len(ax["tainted"])
        if established > best_established:
            best_established, best_spec, stale = established, tools.read_out(deps, impl), 0
        else:
            stale += 1
            tools.write_out(deps, impl, best_spec)
            lean.build(deps)
            ax = lean.check_axioms(deps, impl)
        log.info("PROVE round %d: %d/%d established (best=%d, stale=%d/%d)",
                 rnd, established, total, best_established, stale, config.STALL_ROUNDS)
        if stop or not ax["tainted"] or stale >= config.STALL_ROUNDS:
            break
        prompt = _prove_feedback(deps, ax)

    tools.write_out(deps, impl, best_spec)
    if not lean.build(deps).get("success"):
        raise _PipelineAborted("PROVE left the spec in a non-compiling state")
    tools.commit(deps.container_id, "stage/prove: proof attempts")
    deps.progress["proofs_done"] = True
    _record_axioms(deps)


# ── REPORT ──────────────────────────────────────────────────────────────────────

_REPORT_SECTIONS = ["report/01_overview.md", "report/02_translation.md",
                    "report/03_implementation_spec.md", "report/04_spec_judge.md",
                    "report/05_proofs.md", "report/06_summary.md"]


def _authoritative_verdict(deps: AgentDeps) -> str:
    """The soundness headline, generated by the HARNESS directly from the `#print axioms` gate
    (progress['axioms']) — never from the REPORT agent's narrative. Prepended to
    VERIFICATION_REPORT.md so the human-facing verdict is the kernel's, immune to an agent that
    might miscount a *compiling* proof as verified (a proof can compile yet rest on a non-standard
    axiom — `sorryAx`, `decide`/`native_decide` compiler trust, an opaqued primitive — which is
    exactly what `#print axioms` catches and the count below reflects)."""
    ax = deps.progress.get("axioms", {})
    clean, tainted = ax.get("clean", []), ax.get("tainted", [])
    impl, abstract = ax.get("impl_verified", []), ax.get("abstract_only", [])
    total = len(clean) + len(tainted)
    return "\n".join([
        "# Verification verdict — AUTHORITATIVE (Lean `#print axioms`, harness-generated)",
        "",
        "> Generated by the harness directly from the kernel `#print axioms` gate — the definitive "
        "result. A theorem that COMPILES is not necessarily established: a proof can rest on a "
        "non-standard axiom (`sorryAx`, `decide`/`native_decide` compiler trust, or an "
        "assumed/opaqued primitive), and only the count below — not \"has a proof body\" — reflects "
        "what the kernel actually accepts. Any narrative in the sections that follow which conflicts "
        "with this block is wrong.",
        "",
        f"- **Theorems that VERIFY THE IMPLEMENTATION: {len(impl)} / {total}** "
        f"(kernel-established on standard axioms only, AND referencing an Aeneas-translated def):",
        f"  {impl or '(none)'}",
        f"- Abstract-only established lemmas (established, but not about the implementation): "
        f"{len(abstract)} {abstract or ''}",
        f"- NOT established — **tainted, verify NOTHING** (a leftover `sorry`, or a proof resting on "
        f"a non-standard axiom): {len(tainted)} {tainted or ''}",
        "",
        "How the target was translated — scope / opaqued leaves / any rung-3 modeling — is recorded "
        "in `translate/accountability.md` and summarised in §2 below.",
        "",
        "---",
        "",
    ])


def _stage_report(deps: AgentDeps) -> str:
    """Prepare the authoritative facts.json (the harness's trusted verdicts), run the REPORT CC
    session, then concatenate its report/NN_*.md sections into VERIFICATION_REPORT.md."""
    facts = {
        "axioms": deps.progress.get("axioms", {}),
        "spec_judge": deps.progress.get("verdict", {}),
        "target_patterns": deps.progress.get("target_patterns", []),
        "opaque_assumptions": lean.external_axioms(lean.translation_text(deps)),
        "holes": deps.progress.get("aeneas", {}).get("holes", []),
        "lean_path": deps.progress.get("aeneas", {}).get("lean_path", ""),
        "lean_build_success": deps.progress.get("lean_build", {}).get("success"),
        "sorry_remaining": max(lean.sorry_count(deps), 0),
    }
    tools.write_out(deps, "report/facts.json", json.dumps(facts, indent=2))
    try:
        run_cc_stage(
            deps, stage="REPORT", briefing=briefings.REPORT,
            prompt=("Proceed to REPORT. Read /workspace/out/report/facts.json (the authoritative "
                    "verdicts) and the artefacts under /workspace/out, then write the six "
                    "report/NN_*.md section files per your briefing. Do not write VERIFICATION_REPORT.md."),
            **_cc_common())
    except StageFailed as e:
        log.warning("REPORT session errored (%s) — concatenating whatever sections exist", e)

    parts = [c for sf in _REPORT_SECTIONS for c in [tools.read_out(deps, sf)]
             if not c.startswith("ERROR:")]
    # The harness's authoritative verdict ALWAYS leads the report — the agent's sections cannot
    # override the kernel gate's soundness numbers, only elaborate on them.
    report_text = _authoritative_verdict(deps) + "\n\n".join(parts) if parts else ""
    if parts:
        log.info("Concatenating %d/%d report sections under the authoritative verdict block",
                 len(parts), len(_REPORT_SECTIONS))
    else:
        log.warning("REPORT produced no sections")
    if report_text:
        tools.write_out(deps, "VERIFICATION_REPORT.md", report_text)
        tools.commit(deps.container_id, "stage/report: final pipeline report")
        log.info("Report written and committed (%d chars)", len(report_text))
    checkpoint.snapshot(deps)
    return report_text or "(no report generated)"


# ── abort notes ───────────────────────────────────────────────────────────────────

def _write_abort_notes(deps: AgentDeps, reason: str) -> None:
    """Drop a committed 'incomplete verification' artefact summarising what was reached, so an
    aborted run leaves something actionable rather than empty output."""
    p = deps.progress
    stages = [
        ("explore",       "EXPLORE — orientation + toolchain assessment"),
        ("informal_spec", "INFER — behaviour spec + target scope (pristine source)"),
        ("aeneas",        "TRANSLATE — Aeneas translation of the target"),
        ("spec_ok",       "FORMALISE + SPEC-JUDGE — implementation spec (statements)"),
        ("proofs_done",   "PROVE — proofs attempted"),
    ]
    done = [f"- {label}" for key, label in stages if key in p] or ["- (nothing yet)"]
    todo = [f"- {label}" for key, label in stages if key not in p] or ["- (all reached)"]
    lines = ["# Verification incomplete", "",
             f"The pipeline aborted before finishing. **Reason:** {reason}", "",
             "## Completed", *done, "", "## Not reached", *todo, ""]
    if "aeneas" in p:
        holes = p["aeneas"].get("holes", [])
        lines += ["## Translation", f"- Untranslated Aeneas holes: {', '.join(holes) or 'none'}", ""]
    build = p.get("lean_build", {})
    if build and not build.get("success"):
        lines += ["## Last build errors (the spec did not compile)",
                  "```", (build.get("stderr", "") or "")[:3000], "```", ""]
    defects = (p.get("verdict") or {}).get("defects", [])
    if defects:
        lines += ["## Outstanding spec-judge defects",
                  *(f"- {d.get('theorem')} [{d.get('kind')}]: {d.get('detail')}" for d in defects), ""]
    costs = p.get("cc_costs", {})
    if costs:
        lines += ["## Cost so far (per stage, USD)",
                  *(f"- {s}: ${c:.4f}" for s, c in costs.items()), ""]
    lines += ["## What this means",
              "No end-to-end verification was produced. Review the artefacts (specs/, lean/) and the "
              "reason above, address the blocker, then re-run.", ""]
    try:
        tools.write_out(deps, "VERIFICATION_INCOMPLETE.md", "\n".join(lines))
        tools.commit(deps.container_id, "chore: incomplete-verification notes")
        log.info("Wrote VERIFICATION_INCOMPLETE.md (abort notes)")
    except Exception as e:
        log.warning("Could not write abort notes: %s", e)


# ── main entry point ────────────────────────────────────────────────────────────

async def run_session(deps: AgentDeps) -> str:
    """Drive the pipeline stage by stage (each a Claude Code session) and return a final summary.
    Every stage self-skips when its progress marker is present, so a resume re-enters at the first
    incomplete stage. All failure modes degrade to a graceful stop + keep-alive."""
    if deps.progress:
        log.info("Resuming session — completed markers: %s", sorted(deps.progress.keys()))
    try:
        _stage_explore(deps)
        if config.STOP_AFTER_EXPLORE:
            return "Stopped after EXPLORE (LUSTERNA_STOP_AFTER_EXPLORE)."
        _stage_infer(deps)
        _stage_translate(deps)
        if config.STOP_AFTER_TRANSLATE:
            return "Stopped after TRANSLATE (LUSTERNA_STOP_AFTER_TRANSLATE)."
        _stage_formalise(deps)
        if config.STOP_BEFORE_PROVE:
            return "Stopped before PROVE (LUSTERNA_STOP_BEFORE_PROVE)."
        _stage_prove(deps)
        result = _stage_report(deps)

        tools.prune_stray_specs(deps, deps.progress.get("aeneas", {}).get("lean_path", ""))
        tools.commit(deps.container_id, "chore: prune stray spec files")

        total_cost = sum(deps.progress.get("cc_costs", {}).values())
        log.info("Pipeline complete — total Claude Code cost: $%.4f across stages %s",
                 total_cost, deps.progress.get("cc_costs", {}))
        deps.completed = True
        return result

    except (_PipelineAborted, StageFailed) as e:
        # Graceful stop, completed=False: snapshot progress (a resume re-enters at the first
        # incomplete stage), write the accountability note, return a reason — never a traceback.
        # cli.py's finally keeps the container alive for resume. (Provider/budget failures are now
        # handled inside Claude Code via its own retry + --max-budget-usd, so they never reach here.)
        reason = ("a stage session failed unrecoverably" if isinstance(e, StageFailed)
                  else "pipeline aborted")
        log.error("Pipeline stopped — %s: %s", reason, e)
        checkpoint.snapshot(deps)
        _write_abort_notes(deps, f"{reason}: {e}")
        return f"Stopped — {reason}: {e}"
