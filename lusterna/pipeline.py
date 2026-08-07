"""The pipeline spine (spawn model): a deterministic sequencer that runs each stage as a headless
Claude Code session (runner.run_cc_stage) and applies the TRUSTED, agent-inaccessible mechanical
gates to the files the session produces. The agents own the labor; this module owns the trust.

Stages: EXPLORE → INFER → TRANSLATE (+ TRANSLATE-JUDGE) → FORMALISE (+ SPEC-JUDGE) → PROVE → REPORT.
Each stage's deliverable is FILES under /workspace/out; the harness reads them and gates:
  • the compile gate — `lake build` (lean.build / lean.translation_compiles),
  • the soundness gate — `#print axioms` (lean.check_axioms), the authoritative established verdict,
  • no proof smuggling — lean.stub_proofs re-stubs FORMALISE's theorem bodies before acceptance,
  • the audit trail — the source-edit git diff vs the pristine root (tools.repo_diff).
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
    --max-budget-usd bounds each round's cost. This gate-feedback loop is for stages with a gate that
    must PASS (a spec that must compile / clear the judge); PROVE has no such gate (leftover `sorry`
    is a valid outcome), so it runs as a single budgeted session with no iteration.
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


# ── per-campaign artefact paths ───────────────────────────────────────────────────
# Every stage's per-run reasoning is namespaced by campaign, so runs ACCUMULATE and never
# overwrite a prior campaign's. Only the TRANSLATION itself (lean/<Crate>.lean + submodules, and
# translate/probe/) is shared substrate, reused/extended across campaigns.

def _explore_dir(deps: AgentDeps) -> str:
    return f"explore/campaigns/{deps.campaign}"

def _infer_spec(deps: AgentDeps) -> str:
    return f"infer/campaigns/{deps.campaign}.json"

def _translate_dir(deps: AgentDeps) -> str:
    return f"translate/campaigns/{deps.campaign}"


# ── EXPLORE ─────────────────────────────────────────────────────────────────────

def _stage_explore(deps: AgentDeps) -> None:
    if "explore" in deps.progress:
        return
    hint = (f"\n\nDesign focus hint (optional, non-authoritative — which code matters):\n"
            f"{deps.design_doc.rstrip()}" if deps.design_doc.strip() else "")
    xdir = _explore_dir(deps)
    run_cc_stage(
        deps, stage="EXPLORE", briefing=briefings.EXPLORE,
        prompt=(f"Orient to the code at /workspace/repo and run the toolchain reality-check, then "
                f"write /workspace/out/{xdir}/assessment.md and the machine-readable "
                f"/workspace/out/{xdir}/handoff.json per your briefing. Stop once handoff.json "
                f"exists and is valid." + hint),
        **_cc_common(),
    )
    handoff = tools.read_out(deps, f"{xdir}/handoff.json")
    if handoff.startswith("ERROR:"):
        raise _PipelineAborted(f"EXPLORE produced no /workspace/out/{xdir}/handoff.json")
    try:
        deps.progress["explore"] = json.loads(handoff)
    except json.JSONDecodeError as e:
        raise _PipelineAborted(f"EXPLORE handoff.json is not valid JSON: {e}")
    # Snapshot EXPLORE's output + any build-env prep as the branch's first stage commit.
    tools.commit(deps.container_id, "chore(explore): assessment + build-env prep")
    checkpoint.snapshot(deps)


# ── INFER ───────────────────────────────────────────────────────────────────────

def _stage_infer(deps: AgentDeps) -> None:
    if "informal_spec" in deps.progress:
        return
    hint = (f"\n\nDesign focus hint (optional, non-authoritative):\n{deps.design_doc.rstrip()}"
            if deps.design_doc.strip() else "")

    ispec = _infer_spec(deps)

    def check(deps: AgentDeps):
        raw = tools.read_out(deps, ispec)
        if raw.startswith("ERROR:"):
            return False, f"{ispec} is missing — write it per the briefing."
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError as e:
            return False, f"{ispec} is not valid JSON: {e}"
        if "target_patterns" not in spec:
            return False, f"{ispec} is missing the required key `target_patterns`."
        deps.progress["informal_spec"] = spec
        deps.progress["target_patterns"] = list(spec.get("target_patterns") or [])
        return True, ""

    _cc_gate_loop(
        deps, stage="INFER", briefing=briefings.INFER,
        base_prompt=(f"Proceed to INFER. Read the pristine Rust at /workspace/repo and "
                     f"/workspace/out/{_explore_dir(deps)}/handoff.json, then write "
                     f"/workspace/out/{ispec} per your briefing." + hint),
        check=check,
    )
    tools.commit(deps.container_id, "feat(spec): informal specification (pre-translate)")
    checkpoint.snapshot(deps)   # commit-then-snapshot: git_head must track this stage's own commit
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
        tdir = _translate_dir(deps)
        judge_facts = {
            "compiles": True,
            "emitted_axioms": lean.external_axioms(lean.translation_text(deps, facts["lean_files"])),
            "generated_lean_files": facts["lean_files"],
            "source_files_changed": tools.repo_changed_files(deps.container_id),
            "source_git_diff": tools.repo_diff(deps.container_id),
        }
        tools.write_out(deps, f"{tdir}/facts.json", json.dumps(judge_facts, indent=2))
        verdict = _run_judge(
            deps, stage="TRANSLATE-JUDGE", briefing=briefings.TRANSLATE_JUDGE,
            prompt=(f"Judge the translation in /workspace/out/lean against the target source in "
                    f"/workspace/repo. The target functions are: {targets or '(whole crate)'}. Read "
                    f"the facts at /workspace/out/{tdir}/facts.json and this campaign's accountability "
                    f"at /workspace/out/{tdir}/accountability.md, then write your verdict to "
                    f"/workspace/out/{tdir}/verdict.json per your briefing."),
            verdict_rel=f"{tdir}/verdict.json")
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
                     f"/workspace/out/{_infer_spec(deps)} and /workspace/out/{_explore_dir(deps)}/handoff.json, "
                     f"then drive Charon + Aeneas into /workspace/out/lean (the SHARED translation — "
                     f"reuse/extend it, do not restart from scratch). Record THIS campaign's decision "
                     f"(reused verbatim / extended with <defs> / any source edit) in "
                     f"/workspace/out/{_translate_dir(deps)}/accountability.md per your briefing."),
        check=check,
    )
    tools.commit(deps.container_id, f"feat(translate): {deps.campaign} — translation reuse/extend")
    checkpoint.snapshot(deps)   # commit-then-snapshot: git_head must track this stage's own commit


# ── FORMALISE (+ SPEC-JUDGE) ──────────────────────────────────────────────────────

def _restore_prior_specs(deps: AgentDeps) -> None:
    """Prior campaigns' spec modules are IMMUTABLE. Restore every Spec/*.lean present in the seed
    (refs/lusterna/pristine) EXCEPT the current campaign's, so a new campaign can neither edit nor
    smuggle a proof into an earlier one — it must reuse prior lemmas by `import`. On a fresh run the
    seed has no spec modules, so this is a no-op."""
    stem = lean._crate_stem(deps)
    if not stem:
        return
    current = lean.campaign_spec(deps)
    cid = deps.container_id
    _, out, _ = container.exec_in(
        cid, ["git", "ls-tree", "-r", "--name-only", "refs/lusterna/pristine",
              f"verification/lean/{stem}/Spec/"], workdir=container.REPO_IN)
    for gitpath in (l.strip() for l in out.splitlines() if l.strip().endswith(".lean")):
        if gitpath.removeprefix("verification/") == current:
            continue
        container.exec_in(cid, ["git", "checkout", "refs/lusterna/pristine", "--", gitpath],
                          workdir=container.REPO_IN)


def _stage_formalise(deps: AgentDeps) -> None:
    if "spec_ok" in deps.progress:
        return
    impl = lean.campaign_spec(deps)

    def check(deps: AgentDeps):
        _restore_prior_specs(deps)   # prior campaigns' modules are immutable — revert any edits
        raw = tools.read_out(deps, impl)
        if raw.startswith("ERROR:"):
            return False, f"the spec {impl} is missing — write the statement-only spec there."
        # TRUSTED no-smuggle gate: force every theorem body in THIS campaign's module to `:= by sorry`
        # before acceptance, so FORMALISE cannot sneak a proof past PROVE. Then the compile gate.
        tools.write_out(deps, impl, lean.stub_proofs(raw))
        deps.progress["formal_spec"] = True
        if not lean.build(deps).get("success"):
            err = deps.progress.get("lean_build", {}).get("stderr", "")
            return False, "the theorem STATEMENTS do not compile (you still write NO proofs):\n" + err[-1500:]
        verdict = _run_judge(
            deps, stage="SPEC-JUDGE", briefing=briefings.SPEC_JUDGE,
            prompt=(f"Judge the theorem STATEMENTS in /workspace/out/{impl} against the translation "
                    f"in /workspace/out/lean and /workspace/out/{_infer_spec(deps)}, then write "
                    f"your verdict to /workspace/out/spec-judge/verdict.json per your briefing."),
            verdict_rel="spec-judge/verdict.json")
        deps.progress["verdict"] = verdict
        defects = verdict.get("defects", [])
        if defects:
            return False, "SPEC-JUDGE found defects in the statements:\n" + _format_defects(defects)
        deps.progress["spec_ok"] = True
        return True, ""

    _cc_gate_loop(
        deps, stage="FORMALISE", briefing=briefings.FORMALISE,
        base_prompt=(f"Proceed to FORMALISE. Read /workspace/out/{_infer_spec(deps)} and the "
                     f"translation under /workspace/out/lean, then write the statement-only spec "
                     f"(theorem bodies `:= by sorry`) for THIS campaign to /workspace/out/{impl} "
                     f"per your briefing. It is a NEW module — do NOT edit or delete any other "
                     f"lean/**/Spec/*.lean (prior campaigns); reuse their lemmas by `import`. "
                     f"Ensure it compiles with `lake env lean`."),
        check=check,
    )
    tools.commit(deps.container_id, "feat(spec): implementation spec (statements only)")
    checkpoint.snapshot(deps)   # commit-then-snapshot: git_head must track this stage's own commit


# ── PROVE ─────────────────────────────────────────────────────────────────────────


def _verifies_impl(spec_text: str, translation: str, name: str) -> bool:
    """True if theorem *name*'s statement references a translated def — i.e. it is about the
    implementation (a candidate goal), not an abstract helper lemma."""
    stmt = lean.theorem_statement(spec_text, name)
    return bool(stmt and lean.referenced_defs(stmt, translation))


def _record_axioms(deps: AgentDeps) -> None:
    """`#print axioms` (standard-axioms-only) + partition established theorems into those that VERIFY
    THE IMPLEMENTATION (reference a real Aeneas def) vs abstract helper lemmas.

    The harness does NOT re-derive a faithfulness tier: rung-3 modeling is a sanctioned capability,
    screened once (semantically) by the TRANSLATE-JUDGE and disclosed by the agent in
    translate/accountability.md — that trail is the record, not a redundant harness verdict.

    CUMULATIVE across all campaign modules (lean/<Crate>/Spec/*.lean): each campaign's established
    theorems keep counting, so the verdict reflects every campaign run against this target, not just
    the latest. Names are campaign-qualified (`<Campaign>::<theorem>`) to show provenance."""
    from pathlib import Path as _P
    translation = lean.translation_text(deps)
    bad = {v["axiom"] for v in lean.legitimacy_check(deps, translation)}   # fail-closed set
    clean, assumed, tainted = [], {}, []
    impl_verified, impl_verified_assumed, abstract_only = [], [], []
    for mod in lean.spec_modules(deps):
        camp = _P(mod).stem
        ax = lean.check_axioms(deps, mod)
        spec_text = tools.read_out(deps, mod)
        clean += [f"{camp}::{n}" for n in ax["clean"]]
        for n in ax["clean"]:
            dst = impl_verified if _verifies_impl(spec_text, translation, n) else abstract_only
            dst.append(f"{camp}::{n}")
        for n, used in ax["assumed"].items():
            q = f"{camp}::{n}"
            if set(used) & bad:                          # leans on an inadmissible axiom → fail-closed
                tainted.append(q)
                continue
            assumed[q] = used
            dst = impl_verified_assumed if _verifies_impl(spec_text, translation, n) else abstract_only
            dst.append(q)
        tainted += [f"{camp}::{n}" for n in ax["tainted"]]

    deps.progress["axioms"] = {
        "clean": clean, "assumed": assumed, "tainted": tainted,
        "impl_verified": impl_verified, "impl_verified_assumed": impl_verified_assumed,
        "abstract_only": abstract_only,
        "declared_assumptions": sorted(lean.declared_assumptions(deps)),
        "illegitimate_assumptions": sorted(bad),
    }
    checkpoint.snapshot(deps)
    established = len(clean) + len(assumed)
    if bad:
        log.warning("PROVE: ⚠ CRITICAL — %d declared assumption(s) ILLEGITIMATE (reference a target; "
                    "would relax a goal); theorems leaning on them DEMOTED to tainted: %s", len(bad),
                    sorted(bad))
    if established and not (impl_verified or impl_verified_assumed):
        log.warning("PROVE: ⚠ CRITICAL — %d theorem(s) established but NONE reference the "
                    "implementation; 0 properties of the code are verified (abstract only: %s)",
                    established, abstract_only)
    log.info("PROVE complete — %d sorry remaining; established %d (%d clean + %d assumed) of %d; "
             "impl-verified %d clean + %d modulo-base; abstract-only %d; tainted %d",
             max(lean.sorry_count(deps), 0), established, len(clean), len(assumed),
             established + len(tainted), len(impl_verified), len(impl_verified_assumed),
             len(abstract_only), len(tainted))


def _stage_prove(deps: AgentDeps) -> None:
    """One budgeted, resume-aware PROVE session — the agent, not the harness, drives the proof.

    Like EXPLORE/INFER/REPORT (and unlike the old scored, stall-detected, rollback loop this
    replaces), PROVE runs the agent ONCE and lets it work: it develops a cumulative Lean library —
    helper lemmas, `@[progress]` loop-spec lemmas, trusted assumptions, target proofs — and COMMITS
    as it goes. The per-stage `--max-budget-usd` is the hard stop; the session ends when the agent is
    done (leftover `sorry` is honest) or the budget backstop trips. The harness does not referee: git
    is both the persistence AND the safety net (a red tree falls back to the agent's own last commit),
    and `#print axioms` over the committed library is the sole, mechanical arbiter."""
    if "proofs_done" in deps.progress:
        return
    if not lean.build(deps).get("success"):
        raise _PipelineAborted("PROVE not started — the implementation spec does not compile")

    sid = deps.progress.get("cc_sessions", {}).get("PROVE")
    prompt = ("Proceed to PROVE. Discharge as many `sorry` theorems in this campaign's spec module as "
              "you can, WITHOUT changing any statement. Build up and COMMIT whatever supporting lemmas "
              "you need across the Lean library, reuse them by import, keep it compiling and commit as "
              "you go. Leaving genuinely hard theorems as `sorry` is fine. Follow your briefing.")
    try:
        run_cc_stage(deps, stage="PROVE", prompt=prompt,
                     briefing=(None if sid else briefings.PROVE), resume_sid=sid, **_cc_common())
    except StageFailed as e:
        log.warning("PROVE session produced no result (%s) — gating on-disk regardless", e)
    checkpoint.snapshot(deps)

    # Git is the net: if the agent left the tree non-compiling, fall back to its own last commit (by
    # discipline a green state) rather than a harness-tracked snapshot. A green tree is committed as-is.
    if not lean.build(deps).get("success"):
        log.warning("PROVE left a non-compiling tree — resetting to the agent's last commit")
        container.exec_in(deps.container_id, ["git", "reset", "--hard", "HEAD"],
                          workdir=container.REPO_IN)
        lean.build(deps)
    tools.commit(deps.container_id, "stage/prove: proof attempts")
    deps.progress["proofs_done"] = True
    _record_axioms(deps)


# ── REPORT ──────────────────────────────────────────────────────────────────────

_SECTION_NAMES = ["01_overview.md", "02_translation.md", "03_implementation_spec.md",
                  "04_spec_judge.md", "05_proofs.md", "06_summary.md"]


def _campaigns_index(deps: AgentDeps) -> str:
    """A bullet index of every per-campaign report under report/campaigns/*.md — the cumulative
    top-level VERIFICATION_REPORT.md links to each, so no campaign's report is ever hidden."""
    _, out, _ = container.exec_in(
        deps.container_id, ["sh", "-c", f"find {container.OUT_IN}/report/campaigns -maxdepth 1 "
                                        f"-name '*.md' 2>/dev/null | sort"])
    from pathlib import Path as _P
    names = [_P(l.strip()).stem for l in out.splitlines() if l.strip()]
    return "\n".join(["## Campaigns", ""] + [f"- [{n}](report/campaigns/{n}.md)" for n in names]) + "\n"


def _authoritative_verdict(deps: AgentDeps) -> str:
    """The soundness headline, generated by the HARNESS directly from the `#print axioms` gate
    (progress['axioms']) — never from the REPORT agent's narrative. Prepended to
    VERIFICATION_REPORT.md so the human-facing verdict is the kernel's, immune to an agent that
    might miscount a *compiling* proof as verified (a proof can compile yet rest on a non-standard
    axiom — `sorryAx`, `decide`/`native_decide` compiler trust, an opaqued primitive — which is
    exactly what `#print axioms` catches and the count below reflects)."""
    ax = deps.progress.get("axioms", {})
    clean, tainted = ax.get("clean", []), ax.get("tainted", [])
    assumed = ax.get("assumed", {})                        # {thm: [assumption qnames]}
    impl, abstract = ax.get("impl_verified", []), ax.get("abstract_only", [])
    impl_assumed = ax.get("impl_verified_assumed", [])
    declared = ax.get("declared_assumptions", [])
    illegit = ax.get("illegitimate_assumptions", [])
    total = len(clean) + len(assumed) + len(tainted)
    lines = [
        "# Verification verdict — AUTHORITATIVE (Lean `#print axioms`, harness-generated)",
        "",
        "> Generated by the harness directly from the kernel `#print axioms` gate — the definitive "
        "result. A theorem that COMPILES is not necessarily established: a proof can rest on a "
        "non-standard axiom (`sorryAx`, `decide`/`native_decide` compiler trust, or an opaqued "
        "primitive), and only the counts below reflect what the kernel accepts. Results split into "
        "established on STANDARD axioms vs established MODULO the trusted base (declared assumptions, "
        "listed below — trusted, not proved). Any narrative that conflicts with this block is wrong.",
        "",
        f"- **VERIFY THE IMPLEMENTATION on standard axioms: {len(impl)} / {total}** "
        f"(kernel-established, standard axioms only, referencing an Aeneas-translated def):",
        f"  {impl or '(none)'}",
    ]
    if impl_assumed or declared:
        lines += [
            f"- **VERIFY THE IMPLEMENTATION modulo the trusted base: {len(impl_assumed)}** "
            f"(established and about the implementation, but resting on ≥1 assumed fact below — "
            f"\"verified modulo the trusted base\", NOT unconditionally verified):",
            f"  {impl_assumed or '(none)'}",
        ]
    lines += [
        f"- Abstract-only established lemmas (not about the implementation): {len(abstract)} {abstract or ''}",
        f"- NOT established — **tainted, verify NOTHING** (a leftover `sorry`, native_decide, or an "
        f"undeclared axiom): {len(tainted)} {tainted or ''}",
        "",
    ]
    if declared or illegit:
        used_by: dict[str, list] = {}
        for thm, used in assumed.items():
            for a in used:
                used_by.setdefault(a, []).append(thm)
        lines += ["## Trusted base — assumed, NOT proved", "",
                  "These facts are ASSUMED (declared `axiom`s in "
                  f"`{lean.assumptions_module(deps)}`): trusted, not proved. Every result marked "
                  "\"modulo the trusted base\" depends on them; each must be reviewed, and can be "
                  "discharged later by proving it (e.g. against a value model) to retire the trust."]
        for a in declared:
            flag = ("  ⚠ ILLEGITIMATE (references a target — dependents demoted to tainted)"
                    if a in illegit else "")
            lines.append(f"- `{a}` — relied on by: {used_by.get(a) or '(nothing established)'}{flag}")
        if illegit:
            lines += ["", f"⚠ {len(illegit)} declared assumption(s) ILLEGITIMATE and NOT honored: "
                      f"{illegit}. An assumption may not reference a target function (that would relax a "
                      f"goal); such facts must be proved, not assumed."]
        lines.append("")
    lines += [
        "How the target was translated — scope / opaqued leaves / any rung-3 modeling — is recorded "
        f"in `{_translate_dir(deps)}/accountability.md` and summarised in §2 below.",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def _stage_report(deps: AgentDeps) -> str:
    """Write the CURRENT campaign's report as report/campaigns/<Campaign>.md (never overwriting a
    prior campaign's), and regenerate the cumulative top-level VERIFICATION_REPORT.md — the
    authoritative `#print axioms` verdict (spanning ALL campaign modules) + an index of every
    per-campaign report. report/axioms.json holds the cumulative facts."""
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
    tools.write_out(deps, "report/axioms.json", json.dumps(facts, indent=2))
    secdir = f"report/campaigns/{deps.campaign}"
    try:
        run_cc_stage(
            deps, stage="REPORT", briefing=briefings.REPORT,
            prompt=(f"Proceed to REPORT for the '{deps.campaign}' campaign. Read "
                    f"/workspace/out/report/axioms.json (the authoritative cumulative verdicts) and "
                    f"the artefacts under /workspace/out, then write the six section files to "
                    f"/workspace/out/{secdir}/NN_*.md per your briefing. Do not write "
                    f"VERIFICATION_REPORT.md or touch other campaigns' reports."),
            **_cc_common())
    except StageFailed as e:
        log.warning("REPORT session errored (%s) — concatenating whatever sections exist", e)

    parts = [c for n in _SECTION_NAMES for c in [tools.read_out(deps, f"{secdir}/{n}")]
             if not c.startswith("ERROR:")]
    # Per-campaign report (never clobbers a sibling campaign's).
    if parts:
        campaign_report = f"# Campaign report — {deps.campaign}\n\n" + "\n\n".join(parts)
        tools.write_out(deps, f"report/campaigns/{deps.campaign}.md", campaign_report)
        log.info("Wrote report/campaigns/%s.md (%d/%d sections)",
                 deps.campaign, len(parts), len(_SECTION_NAMES))
    else:
        log.warning("REPORT produced no sections for campaign %s", deps.campaign)
    # Cumulative top-level: the harness's authoritative verdict (all campaign modules) leads, then
    # an index of every campaign report. Regenerated each run — it is MEANT to be cumulative.
    report_text = _authoritative_verdict(deps) + _campaigns_index(deps)
    tools.write_out(deps, "VERIFICATION_REPORT.md", report_text)
    tools.commit(deps.container_id, f"stage/report: {deps.campaign} campaign report + cumulative index")
    checkpoint.snapshot(deps)
    return (f"report/campaigns/{deps.campaign}.md" if parts else "(no report generated)")


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
              "No end-to-end verification was produced. Review the artefacts (infer/, lean/) and the "
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
