"""The pipeline itself: the stage phases (SpecPhase, ProvePhase) and the linear stage
drivers, sequenced by run_session. Companion modules: stages.py declares the stage agents;
runner.py has the generic stage-runner and tool wiring; lean.py the Aeneas/Lean operations;
checkpoint.py the state snapshots. Each stage runs with no shared message history — stages
communicate via the filesystem and deps.progress."""
import json
import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded

from . import checkpoint, lean, telemetry, tools
from .schemas import (
    AbstractInformalSpec, AbstractFormalSpec,
    InformalSpec, FormalSpec, JudgeVerdict, ReconciliationReport,
)
from .runner import _run_stage
from .stages import (
    doc_infer as _doc_infer, doc_formalise as _doc_formalise, explore as _explore,
    infer as _infer, formalise as _formalise,
    judge as _judge, reconcile as _reconcile, prove as _prove, report as _report,
    translate_remediate as _translate_remediate,
)
from .schemas import AgentDeps
from . import config

log = logging.getLogger(__name__)


class _PipelineAborted(Exception):
    pass


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
        checkpoint.snapshot(deps)

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
            checkpoint.snapshot(deps)
        else:
            log.warning("DOC-FORMALISE produced no output")

    return resume_note


class TranslatePhase:
    """Target-scoped TRANSLATE with a soundness-graded remediation ladder.

    Attempt 0 scopes Charon to the INFER-chosen target (`--start-from`). If that fails or
    leaves holes inside the target's call-closure, a bounded loop asks the remediation agent
    for the next least-invasive action: `--opaque` an in-closure dependency (ASSUMPTION —
    emitted as a Lean `axiom`, caught by the `#print axioms` gate) → behaviour-preserving
    source refactor (MODIFICATION — recorded with a diff) → give up. Every action is written
    to the accountability trail. Non-convergence aborts the pipeline (no partial salvage).
    """

    MAX_ROUNDS = 8   # bounded remediation rounds after the scoped baseline
    MAX_EDITS  = 6   # cap on behaviour-preserving source-refactor rounds

    def __init__(self, deps: AgentDeps, entry: str, resume_note: str):
        self.deps = deps
        self.entry = entry
        self.resume_note = resume_note
        self.target_patterns: list[str] = list(deps.progress.get("target_patterns", []))
        self.trail: list[dict] = []
        self.opaques: list[str] = []
        self.excludes: list[str] = []
        self.edits_used = 0
        self.best: dict | None = None

    async def run(self) -> str:
        if "aeneas" in self.deps.progress:          # resuming past TRANSLATE
            return self.resume_note
        log.info("─── Stage: TRANSLATE (target-scoped: %s) ───",
                 self.target_patterns or "whole crate")

        result = lean.run_aeneas(self.deps, self.entry, start_from=self.target_patterns or None)
        self._record("SCOPE", "SAFE", result,
                     rationale="scope translation to the verification target",
                     scope={"start_from": self.target_patterns})

        # Scope guard: if patterns were given but matched no translated def, the target's
        # behaviour was not translated at all — widen to the whole crate rather than accept
        # an empty scope as "clean" (the pattern-mismatch risk).
        if (result.get("success") and self.target_patterns and not lean.matched_target_defs(
                lean.translation_text(self.deps, result["lean_files"]), self.target_patterns)):
            log.warning("TRANSLATE: target patterns %s matched no translated def — widening to "
                        "whole crate", self.target_patterns)
            self.target_patterns = []
            result = lean.run_aeneas(self.deps, self.entry)
            self._record("SCOPE", "SAFE", result,
                         rationale="target patterns matched no code; widened to whole crate",
                         scope={"start_from": []})

        # Compute blockers ONCE per result (each is a multi-second `lake` build) and thread it
        # through best-tracking, the accept check, the stall signature, and remediation.
        blockers = self._blockers(result)
        self._consider_best(result, blockers)

        last_sig = None
        for _ in range(self.MAX_ROUNDS):
            if result.get("success") and not blockers:
                break                                # translation clean, compiles, no assumptions
            sig = self._signature(result, blockers)
            if sig == last_sig:
                log.warning("TRANSLATE remediation made no change — stopping")
                break
            last_sig = sig
            action = await self._remediate(result, blockers)
            if action is None or action.tier == "give_up":
                if action is not None:
                    self._record("GIVE_UP", "MODIFICATION", result,
                                 rationale=action.rationale)
                break
            result = self._apply(action, result)
            blockers = self._blockers(result)
            self._consider_best(result, blockers)

        return self._finalise()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _blockers(self, result: dict) -> dict:
        """What still prevents accepting this translation of the target. Empty ⇒ accept.
        Per the agreed success criterion: Aeneas output + no target holes + the translation
        compiles + no non-standard external axiom in the target's closure."""
        if not result.get("success"):
            return {"translate_failed": result.get("stage_failed", "?"),
                    "errors": (result.get("aeneas_errors")
                               or [(result.get("charon_errors", "") or "")[:400]])}
        tx = lean.translation_text(self.deps, result["lean_files"])
        b: dict = {}
        th = lean.target_holes(tx, result.get("holes", []), self.target_patterns)
        if th:
            b["target_holes"] = th
        axm = lean.target_external_axioms(tx, self.target_patterns)
        if axm:
            b["external_axioms"] = axm
        build = lean.translation_compiles(self.deps, result.get("lean_path", ""))
        if not build.get("success"):
            b["build_errors"] = (build.get("stderr", "") or "")[:1500]
        return b

    def _signature(self, result: dict, blockers: dict) -> tuple:
        if result.get("success"):
            return ("ok", tuple(sorted(blockers.get("target_holes", []))),
                    tuple(sorted(blockers.get("external_axioms", []))),
                    bool(blockers.get("build_errors")))
        return (result.get("stage_failed", "?"),
                tuple(result.get("aeneas_errors") or [result.get("charon_errors", "")[:200]]))

    def _consider_best(self, result: dict, blockers: dict) -> None:
        if not result.get("success"):
            return
        # Fewer blockers is better; a fully-clean translation (0 blockers) wins outright.
        score = -(len(blockers.get("target_holes", [])) + len(blockers.get("external_axioms", []))
                  + (1 if blockers.get("build_errors") else 0))
        if self.best is None or score > self.best["score"]:
            self.best = {"result": result, "blockers": blockers, "score": score}

    async def _remediate(self, result: dict, blockers: dict):
        summary = self.deps.progress.get("informal_spec", {}).get("summary", "")
        report = {
            "blockers": {
                "translate_failed": blockers.get("translate_failed"),
                "target_holes": blockers.get("target_holes", []),
                "external_axioms_in_target": blockers.get("external_axioms", []),
                "translation_build_errors": bool(blockers.get("build_errors")),
            },
            "build_error_tail": blockers.get("build_errors", "")[:1500],
            "charon_errors": (result.get("charon_errors", "") or "")[:1000],
            "aeneas_errors": result.get("aeneas_errors", [])[:20],
            "holes_by_file": result.get("holes_by_file", {}),
            "target_patterns": self.target_patterns,
            "already_opaque": self.opaques,
            "source_edits_used": f"{self.edits_used}/{self.MAX_EDITS}",
            "trail_so_far": [
                {"round": r["round"], "action": r["action"], "tier": r["tier"],
                 "rationale": r["rationale"]}
                for r in self.trail
            ],
        }
        # Inject the CURRENT source (after any prior edits) so `find` anchors match the real
        # file — otherwise multi-round refactors fail (anchors written against stale text).
        current_source = tools.read_repo_sources(self.deps)
        prompt = (
            f"Target behaviour (INFER summary): {summary}\n\n"
            f"Translation outcome so far (JSON):\n{json.dumps(report, indent=2)}\n\n"
            f"### CURRENT Rust source (AFTER any edits already applied — your `find` anchors "
            f"MUST match this text verbatim; each anchor must occur EXACTLY ONCE)\n{current_source}\n\n"
            "Propose the single next RemediationAction.\n"
            "`external_axioms_in_target` are auto-opaqued stdlib items the target depends on — "
            "`--opaque` does NOT remove them (they are already axioms). Eliminate them with a "
            "behaviour-preserving REFACTOR to what Aeneas can actually translate:\n"
            "  • Aeneas CANNOT translate: `BTreeMap`/`HashMap`, `Option`/`Result` COMBINATORS "
            "(`ok_or`, `copied`, `map`, `unwrap_or`, `?`), iterator-adaptor chains "
            "(`.iter().find()`, `.map()`, `.filter()`), and closures.\n"
            "  • Aeneas CAN translate: plain structs/enums, `Vec` (its Std model), EXPLICIT "
            "index loops (`let mut i = 0; while i < v.len() { … i += 1 }`), primitive integer "
            "ops and `==`/`<` on `u64`/`u128`, pattern `match`.\n"
            "  • So model a map as `Vec<(K,V)>` and write get/insert as EXPLICIT INDEX LOOPS "
            "(NOT `.iter().find()`/`.copied()`); compare keys by their primitive fields "
            "(`v[i].0 == k`); rewrite every `Option`/`Result` combinator and `?` as an explicit "
            "`match`; drop `#[derive(Debug)]` (formatting is irrelevant).\n"
            "Use `opaque` only for a genuine `sorry` hole you accept as an assumption; `give_up` "
            "only if no behaviour-preserving refactor exists."
        )
        try:
            res = await _run_stage(_translate_remediate, prompt, self.deps, "TRANSLATE-REMEDIATE")
        except UnexpectedModelBehavior as exc:
            log.warning("Remediation agent errored (%s) — giving up", exc)
            return None
        return res.output if res else None

    def _apply(self, action, current: dict) -> dict:
        if action.tier == "opaque":
            self.opaques = sorted(set(self.opaques) | set(action.opaque))
            self.excludes = sorted(set(self.excludes) | set(action.exclude))
            result = lean.run_aeneas(
                self.deps, self.entry, start_from=self.target_patterns or None,
                opaque=self.opaques or None, exclude=self.excludes or None,
                include=action.include or None)
            self._record("OPAQUE", "ASSUMPTION", result,
                         rationale=action.rationale, expected_effect=action.expected_effect,
                         scope={"start_from": self.target_patterns,
                                "opaque": self.opaques, "exclude": self.excludes})
            return result
        if action.tier == "refactor":
            if self.edits_used >= self.MAX_EDITS:
                log.warning("TRANSLATE: source-edit cap (%d) reached", self.MAX_EDITS)
                self._record("GIVE_UP", "MODIFICATION", current,
                             rationale="behaviour-preserving-refactor cap reached")
                return current
            carve = tools.carve_source(self.deps, action.source_edits)
            self.edits_used += 1
            result = lean.run_aeneas(
                self.deps, self.entry, start_from=self.target_patterns or None,
                opaque=self.opaques or None, exclude=self.excludes or None)
            src_edits = [{"path": e.path,
                          "justification": e.behavior_preservation_justification,
                          "applied": next((r["applied"] for r in carve["edits"]
                                           if r["path"] == e.path), False)}
                         for e in action.source_edits]
            rec = self._record("REFACTOR", "MODIFICATION", result,
                               rationale=action.rationale,
                               expected_effect=action.expected_effect, source_edits=src_edits)
            rec["carve"] = {"repo_commit": carve.get("commit", ""),
                            "diff": carve.get("diff", "")[:6000]}
            self._persist()      # re-persist trail with the carve diff attached
            return result
        return current

    def _record(self, action: str, tier: str, result: dict, *, rationale: str = "",
                scope: dict | None = None, source_edits: list | None = None,
                expected_effect: str = "") -> dict:
        rec = {
            "round": len(self.trail),
            "action": action, "tier": tier,
            "scope": scope or {}, "source_edits": source_edits or [],
            "rationale": rationale, "expected_effect": expected_effect,
            "result": {
                "success": bool(result.get("success")),
                "stage_failed": result.get("stage_failed", ""),
                "holes": result.get("holes", []),
                "target_holes": (lean.target_holes(
                                    lean.translation_text(self.deps, result["lean_files"]),
                                    result.get("holes", []), self.target_patterns)
                                 if result.get("success") else []),
                "commit": result.get("commit", ""),
            },
        }
        self.trail.append(rec)
        self.deps.progress["translate_trail"] = self.trail
        self._persist()
        return rec

    def _persist(self) -> None:
        tools.write_out(self.deps, "translate/accountability.json", json.dumps(self.trail, indent=2))
        tools.write_out(self.deps, "translate/accountability.md", self._render_md())
        tools.commit(self.deps.container_id,
                     f"translate: remediation trail ({len(self.trail)} action(s))",
                     glob="translate/")
        checkpoint.snapshot(self.deps)

    def _render_md(self) -> str:
        lines = ["# TRANSLATE accountability trail", "",
                 f"Target patterns: `{self.target_patterns or 'whole crate'}`", ""]
        for r in self.trail:
            res = r["result"]
            lines.append(f"## Round {r['round']}: {r['action']} — **{r['tier']}**")
            if r["rationale"]:
                lines.append(f"- Rationale: {r['rationale']}")
            if r["expected_effect"]:
                lines.append(f"- Expected effect: {r['expected_effect']}")
            if r["scope"]:
                lines.append(f"- Scope: `{r['scope']}`")
            for se in r["source_edits"]:
                lines.append(f"- Edit `{se['path']}` (applied={se.get('applied')}): "
                             f"{se['justification']}")
            if r.get("carve", {}).get("diff"):
                lines.append(f"\n```diff\n{r['carve']['diff']}\n```")
            lines.append(f"- Result: success={res['success']} "
                         f"target_holes={res['target_holes']} commit={res['commit'][:8]}")
            lines.append("")
        return "\n".join(lines)

    def _finalise(self) -> str:
        if self.best is None:
            raise _PipelineAborted(
                "TRANSLATE failed — Charon/Aeneas produced no Lean output for the target even "
                "after remediation. See translate/accountability.md for the actions attempted.")
        best_result, blockers = self.best["result"], self.best["blockers"]
        if blockers:
            parts = []
            if blockers.get("target_holes"):
                parts.append(f"{len(blockers['target_holes'])} untranslated hole(s) in the target "
                             f"({blockers['target_holes']})")
            if blockers.get("external_axioms"):
                parts.append(f"{len(blockers['external_axioms'])} auto-opaqued external axiom(s) the "
                             f"target depends on ({blockers['external_axioms']}) — no behaviour-"
                             f"preserving refactor removed them")
            if blockers.get("build_errors"):
                parts.append("the translation does not compile")
            raise _PipelineAborted(
                "TRANSLATE could not produce a verifiable translation of the target after "
                "remediation: " + "; ".join(parts) + ". See translate/accountability.md.")
        # Success: pin the on-disk lean/ to the best translation, then record it.
        tools.restore_lean(self.deps.container_id, best_result.get("commit", ""))
        self.deps.progress["aeneas"] = best_result
        self.deps.progress["translate_trail"] = self.trail
        tiers = {r["tier"] for r in self.trail}
        log.info("TRANSLATE complete — %d action(s), tiers=%s, target fully translated & compiles",
                 len(self.trail), sorted(tiers))
        checkpoint.snapshot(self.deps)
        return ""


async def _run_translate_stages(deps: AgentDeps, resume_note: str) -> str:
    """Run EXPLORE, INFER (on the pristine Rust), then TRANSLATE (scoped + remediation).

    INFER runs BEFORE translation so the behavioural spec + target scope are pinned to the
    original code, not to whatever the remediation loop may refactor. Raises _PipelineAborted
    if INFER produces no spec or TRANSLATE cannot converge.
    """
    completed = set(deps.progress.keys())

    if "explore" not in completed:
        sources = tools.read_repo_sources(deps)
        explore_result = await _run_stage(
            _explore,
            f"Rust repository at {deps.repo_path}. Analyse the following sources:\n\n"
            f"{sources}" + resume_note,
            deps, "EXPLORE",
        )
        if explore_result and explore_result.output:
            deps.progress["explore"] = explore_result.output.model_dump()
        checkpoint.snapshot(deps)
        resume_note = ""
        completed = set(deps.progress.keys())

    if "informal_spec" not in completed:
        # INFER on the PRISTINE Rust source (before any translation/refactor). Derives the
        # actual behaviour AND the Charon target patterns used to scope TRANSLATE.
        sources = tools.read_repo_sources(deps)
        abstract_informal = tools.read_out(deps, "specs/abstract_informal_spec.json")
        entry_functions = deps.progress.get("explore", {}).get("entry_functions", [])
        infer_files = f"### Rust source (pristine, unmodified)\n{sources}"
        if not abstract_informal.startswith("ERROR:"):
            infer_files += ("\n\n### specs/abstract_informal_spec.json (design intent)\n"
                            f"{abstract_informal}")
        if entry_functions:
            infer_files += f"\n\n### Public entry functions (from EXPLORE)\n{entry_functions}"
        infer_result = await _run_stage(
            _infer,
            f"Proceed to INFER. From the following, derive (1) a structured InformalSpec of "
            f"the behaviour of the ORIGINAL code, and (2) the Charon target_patterns scoping "
            f"the verification target:\n\n{infer_files}" + resume_note,
            deps, "INFER",
        )
        if infer_result and infer_result.output:
            spec: InformalSpec = infer_result.output
            deps.progress["informal_spec"] = spec.model_dump()
            deps.progress["target_patterns"] = list(spec.target_patterns)
            tools.write_out(deps, "specs/informal_spec.json", spec.model_dump_json(indent=2))
            tools.commit(deps.container_id,
                         "feat(spec): informal specification (pre-translate)", glob="specs/")
            log.info("INFER target patterns: %s", spec.target_patterns or "(whole crate)")
        checkpoint.snapshot(deps)
        resume_note = ""
        completed = set(deps.progress.keys())
        if "informal_spec" not in completed:
            raise _PipelineAborted("Informal spec inference failed.")

    if "aeneas" not in completed:
        entry = deps.progress.get("explore", {}).get("entry_file", "src/lib.rs")
        resume_note = await TranslatePhase(deps, entry, resume_note).run()
        completed = set(deps.progress.keys())

    return resume_note


def _read_spec_files(deps: AgentDeps) -> str:
    """Read all spec files needed by judge and reconcile stages and return as a formatted block."""
    paths = [
        "specs/abstract_formal_spec.lean",
        "specs/abstract_informal_spec.json",
        "specs/informal_spec.json",
    ]
    if impl := lean.impl_spec(deps):
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
    block = f"### Aeneas-translated crate\n{lean.translation_text(deps)}\n\n"
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
        self.impl_spec = lean.impl_spec(deps)
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
                lean.footprint(self.deps)
            return self.resume_note

        while self.attempt < self.MAX_ROUNDS:
            fs = await self._formalise()
            if fs is None:
                break
            kept = lean.assemble_impl_spec(self.deps, fs, drop=frozenset(self.dropped))
            self.deps.progress["formal_spec"] = True
            if not kept:
                log.warning("FORMALISE: every theorem is quarantined or none was produced — "
                            "nothing left to verify, stopping spec loop")
                break
            if not lean.build(self.deps).get("success"):
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
            lean.build(self.deps)
            log.info("FORMALISE: restored the last compiling spec after a non-compiling final round")

        if not self.deps.progress.get("lean_build", {}).get("success"):
            raise _PipelineAborted(
                f"FORMALISE could not produce a spec whose statements compile "
                f"(after up to {self.MAX_ROUNDS} rounds) — cannot verify")
        lean.footprint(self.deps)   # which Aeneas holes (if any) actually touch the stated properties
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
        judge_files = f"### Aeneas-translated crate\n{lean.translation_text(self.deps)}\n\n"
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
        checkpoint.snapshot(self.deps)
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
    checkpoint.snapshot(deps)


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
        self.impl_spec = lean.impl_spec(deps)
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
        if not lean.build(deps).get("success"):
            raise _PipelineAborted(
                "PROVE not started — the implementation spec does not compile "
                "(FORMALISE did not produce a spec whose statements build)")
        # Baseline best-state = the entry (all-sorry) spec, which just built — the floor PROVE
        # restores to if no proof survives, so it can never abort away a compiling spec.
        deps.progress.pop("prove_best", None)
        lean.record_prove_best(deps)

        try:
            await _run_stage(_prove, self._prompt(), deps, "PROVE",
                             request_limit=config.PROVE_REQUEST_LIMIT, stop_check=self._stop)
        except UnexpectedModelBehavior as e:
            log.warning("PROVE stage failed after retries: %s", e)
        except UsageLimitExceeded as e:
            # Hit the request-count backstop — finalize with whatever it proved (the final build
            # + axiom gate below still run).
            log.warning("PROVE hit the request-limit backstop (%s) — finalizing", e)
        checkpoint.snapshot(deps)
        self._restore_best()
        # The agent may stop before committing — commit here so the proven state lands in git.
        tools.commit(deps.container_id, "stage/prove: proof attempts", glob="lean/")
        # Authoritative final build. With the restore above this is the best verified state, so it
        # builds — the abort is a last-resort invariant check.
        if not lean.build(deps).get("success"):
            raise _PipelineAborted("PROVE left the spec in a non-compiling state")
        deps.progress["proofs_done"] = True
        lean.footprint(deps)   # cheap pre-oracle estimate — proofs may reference defs the stubs did not
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
        translation = lean.translation_text(deps)
        spec_text = tools.read_out(deps, self.impl_spec)
        impl_verified, abstract_only = [], []
        for name in ax["clean"]:
            stmt = lean.theorem_statement(spec_text, name)
            (impl_verified if stmt and lean.referenced_defs(stmt, translation)
             else abstract_only).append(name)
        # Faithfulness tier for the ESTABLISHED (axiom-clean) theorems. Opacity cannot
        # downgrade a clean theorem — one that depended on an `--opaque` axiom would be
        # tainted, not clean — so the only tier the axiom gate can't see is MODIFICATION:
        # a behaviour-preserving source refactor is invisible to `#print axioms`, so if the
        # target was refactored, clean theorems are verified of the REFACTORED code.
        refactored = lean.trail_refactored_paths(deps.progress.get("translate_trail", []))
        faithfulness = "MODIFICATION" if refactored else "SAFE"
        deps.progress["axioms"] = {"clean": ax["clean"], "tainted": ax["tainted"],
                                   "impl_verified": impl_verified, "abstract_only": abstract_only,
                                   "faithfulness": faithfulness, "refactored_paths": refactored}
        checkpoint.snapshot(deps)
        if refactored:
            log.warning("PROVE: established theorems are verified of a REFACTORED implementation "
                        "(behaviour-preserving edits to %s); not verbatim the original.", refactored)
        if ax["clean"] and not impl_verified:
            log.warning("PROVE: ⚠ CRITICAL — %d theorem(s) established but NONE reference the "
                        "implementation; 0 properties of the code are verified. Established are "
                        "abstract helper lemmas only: %s", len(ax["clean"]), abstract_only)
        log.info("PROVE complete — %d sorry remaining; established (standard axioms only): %d/%d "
                 "theorem(s) — %d verify the implementation %s, %d abstract-only %s; tainted=%s",
                 max(lean.sorry_count(deps), 0), len(ax["clean"]),
                 len(ax["clean"]) + len(ax["tainted"]), len(impl_verified), impl_verified,
                 len(abstract_only), abstract_only, ax["tainted"])


async def _run_report(deps: AgentDeps, resume_note: str) -> str:
    """Run the REPORT stage and concatenate section files into VERIFICATION_REPORT.md."""
    spec_defects     = deps.progress.get("verdict", {}).get("defects", [])
    sorry_remaining  = max(lean.sorry_count(deps), 0)
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
    trail = deps.progress.get("translate_trail", [])
    if trail:
        acct = tools.read_out(deps, "translate/accountability.md")
        if not acct.startswith("ERROR:"):
            report_files += f"\n\n### translate/accountability.md\n{acct}"

    # Translation faithfulness line — conditional on the TRANSLATE accountability trail.
    target_patterns = deps.progress.get("target_patterns", [])
    opaque_assumptions = lean.trail_opaque_assumptions(trail)
    refactored_paths = axioms.get("refactored_paths", [])
    if not opaque_assumptions and not refactored_paths:
        translation_line = (
            f"Translation: target scoped via --start-from ({target_patterns or 'whole crate'}); "
            f"source UNMODIFIED — Aeneas ran on the code as written, so the translation is a "
            f"faithful image of the real code. ")
    else:
        _bits = [f"target scope={target_patterns or 'whole crate'}"]
        if opaque_assumptions:
            _bits.append(f"OPAQUE assumptions emitted as Lean axioms {opaque_assumptions} "
                         f"(any theorem depending on them is tainted, not established)")
        if refactored_paths:
            _bits.append(f"SOURCE REFACTORED (behaviour-preserving) in {refactored_paths} — "
                         f"established theorems are verified of the REFACTORED implementation "
                         f"(equivalence asserted + cross-checked by proofs, NOT machine-certified), "
                         f"never claim 'verified the original code'")
        translation_line = ("Translation (SEE translate/accountability.md and report it): "
                            + "; ".join(_bits) + ". ")

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
        + translation_line
        + f"Untranslated holes in crate: {', '.join(holes) if holes else 'none'}. "
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

    checkpoint.snapshot(deps)
    return report_text or "(no report generated)"


def _write_abort_notes(deps: AgentDeps, reason: str) -> None:
    """On a hard abort, drop an 'incomplete verification' notes artefact (committed, so it
    is pulled with the rest of the output) summarising what was accomplished and what could
    not be — so an aborted run leaves something actionable rather than empty output."""
    p = deps.progress
    stages = [
        ("abstract_informal_spec", "DOC-INFER — abstract informal spec"),
        ("abstract_formal_spec",   "DOC-FORMALISE — abstract Lean stubs"),
        ("explore",                "EXPLORE — entry points"),
        ("informal_spec",          "INFER — behaviour spec + target scope (pristine source)"),
        ("aeneas",                 "TRANSLATE — Aeneas translation (scoped)"),
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
    trail = p.get("translate_trail", [])
    if trail:
        lines += ["## TRANSLATE remediation attempted"]
        for r in trail:
            lines.append(f"- Round {r['round']}: {r['action']} ({r['tier']}) — {r['rationale']} "
                         f"→ success={r['result']['success']} "
                         f"target_holes={r['result']['target_holes']}")
        lines += ["", "(full trail: translate/accountability.md)", ""]
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
