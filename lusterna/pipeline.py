"""The pipeline itself: the stage phases (SpecPhase, ProvePhase) and the linear stage
drivers, sequenced by run_session. Companion modules: stages.py declares the stage agents;
runner.py has the generic stage-runner and tool wiring; lean.py the Aeneas/Lean operations;
checkpoint.py the state snapshots. Each stage runs with no shared message history — stages
communicate via the filesystem and deps.progress."""
import json
import logging

from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded

from . import checkpoint, lean, telemetry, tools
from .schemas import InformalSpec, FormalSpec, JudgeVerdict
from .runner import _run_stage
from .stages import (
    explore as _explore, infer as _infer, formalise as _formalise,
    judge as _judge, prove as _prove, report as _report,
    translate as _translate, translate_judge as _translate_judge,
)
from .schemas import AgentDeps
from . import config

log = logging.getLogger(__name__)


class _PipelineAborted(Exception):
    pass


class TranslatePhase:
    """Shell-driven TRANSLATE with a TRANSLATE-JUDGE gate (mirrors SpecPhase's agent↔judge loop).

    The agent drives Charon+Aeneas itself via `bash`, using the least-degrading option that works
    (scope → opaque a leaf → behaviour-preserving edit → give up), and logs every alteration. The
    harness owns only *facts about the artefact* — never *how* to translate:
      • HARD gates (mechanical, agent-inaccessible): the target functions are real `def`s (not
        opaqued to `axiom`, not left as `sorry` holes) AND the translation compiles.
      • SOFT gate: the TRANSLATE-JUDGE screens faithfulness / behaviour-preservation of edits.
    Proof-soundness is NOT decided here — a hole/opaque becomes `sorryAx`/a non-standard axiom and
    is caught downstream by `#print axioms`. Non-convergence aborts (no partial salvage). Every
    source edit is captured as a git diff (accountability trail) for the human reviewer.
    """

    MAX_ROUNDS = 3   # agent+judge rounds before aborting

    def __init__(self, deps: AgentDeps, entry: str, resume_note: str):
        self.deps = deps
        self.entry = entry
        self.resume_note = resume_note
        self.target_patterns: list[str] = list(deps.progress.get("target_patterns", []))

    async def run(self) -> str:
        if "aeneas" in self.deps.progress:          # resuming past TRANSLATE
            return self.resume_note
        log.info("─── Stage: TRANSLATE (target: %s) ───", self.target_patterns or "whole crate")

        feedback = ""
        for rnd in range(self.MAX_ROUNDS):
            outcome = await self._translate(feedback)
            if outcome is not None and outcome.gave_up:
                self._abort(self._facts(), "the TRANSLATE agent gave up — "
                            + (outcome.summary or "no translatable form found"))
            facts = self._facts()
            hard_ok = (facts["success"] and facts["compiles"] and not facts["polluted"]
                       and not facts["target_opaqued"] and not facts["target_holes"])
            if not hard_ok:
                feedback = self._feedback(facts, None)
                log.info("TRANSLATE round %d: hard gate not met", rnd)
                continue
            verdict = await self._judge(facts, outcome)
            defects = list(verdict.defects) if verdict else []
            if not defects:
                return self._accept(facts, outcome)
            feedback = self._feedback(facts, defects)
            log.info("TRANSLATE round %d: judge found %d defect(s)", rnd, len(defects))

        self._abort(self._facts(), f"did not converge on a clean, judge-approved translation in "
                    f"{self.MAX_ROUNDS} rounds")

    # ── agent + judge ─────────────────────────────────────────────────────────
    async def _translate(self, feedback: str):
        prompt = (
            f"Target patterns (translate each to a real `def` — NEVER opaque them): "
            f"{self.target_patterns or '(none — translate the whole crate)'}\n"
            f"Suggested entry file: {self.entry}\n\n"
            "Drive Charon + Aeneas via bash to translate the target into /workspace/out/lean, "
            "least-degrading option first. After Aeneas, call setup_lake_project() and check it "
            "compiles with `lake env lean`. Log every alteration to translate/accountability.md. "
            "Return a TranslateOutcome."
            + (f"\n\n### Problems to FIX from the previous attempt\n{feedback}" if feedback else "")
        )
        try:
            res = await _run_stage(_translate, prompt, self.deps, "TRANSLATE")
        except UnexpectedModelBehavior as exc:
            log.warning("TRANSLATE agent errored (%s)", exc)
            return None
        return res.output if res else None

    async def _judge(self, facts: dict, outcome):
        tx = lean.translation_text(self.deps, facts["lean_files"])
        source = tools.read_repo_sources(self.deps)
        acct = tools.read_out(self.deps, "translate/accountability.md")
        spec = self.deps.progress.get("informal_spec", {})
        props = (json.dumps({k: spec[k] for k in ("summary", "postconditions", "invariants")
                             if spec.get(k)}, indent=2) if spec else "(none inferred)")
        mech = {
            "compiles": facts["compiles"],
            "target_translated_as_def": lean.matched_target_defs(tx, self.target_patterns),
            "target_OPAQUED_defect_if_nonempty": facts["target_opaqued"],
            "target_HOLE_defect_if_nonempty": facts["target_holes"],
            "emitted_axioms_opaqued_assumptions": facts["axioms"],
            "opaqued_items_the_target_calls_directly": lean.opaque_deps_in_targets(
                tx, self.target_patterns),
            "source_files_changed": facts["repo_files"],
        }
        prompt = (
            f"Target patterns: {self.target_patterns or 'whole crate'}\n\n"
            f"### Mechanical facts\n{json.dumps(mech, indent=2)}\n\n"
            f"### Source git diff (judge behaviour-preservation against the original)\n"
            f"{facts['repo_diff'] or '(no source edits)'}\n\n"
            f"### Inferred properties (what will be verified — decide if they depend on any "
            f"opaqued structure)\n{props}\n\n"
            f"### translate/accountability.md\n"
            f"{acct if not acct.startswith('ERROR:') else '(none written)'}\n\n"
            f"### Generated Lean translation\n{tx}\n\n"
            f"### Original Rust source\n{source}\n\n"
            "List every defect as a TranslateVerdict; an empty list approves."
        )
        try:
            res = await _run_stage(_translate_judge, prompt, self.deps, "TRANSLATE-JUDGE")
        except UnexpectedModelBehavior as exc:
            # Judge is the SOFT gate; the hard mechanical gates already passed. On a judge
            # blow-up, accept (do not block on the semantic screen) rather than loop forever.
            log.warning("TRANSLATE-JUDGE errored (%s) — accepting on the hard gates alone", exc)
            return None
        return res.output if res else None

    # ── mechanical facts (agent-inaccessible) ──────────────────────────────────
    def _facts(self) -> dict:
        """Facts about whatever is in /workspace/out/lean — the only thing the harness decides on."""
        lean.setup_lake(self.deps)      # ensure the lake project is wired for the compile check
        info = lean.analyze_translation(self.deps, do_commit=False)
        facts = {
            "success": info["success"], "info": info,
            "lean_files": info["lean_files"], "lean_path": info["lean_path"],
            "holes": info["holes"], "holes_by_file": info["holes_by_file"],
            "compiles": False, "build_errors": "",
            "target_opaqued": [], "target_holes": [], "axioms": [], "polluted": [],
            "repo_diff": tools.repo_diff(self.deps.container_id),
            "repo_files": tools.repo_changed_files(self.deps.container_id),
        }
        # Hygiene: a clean single `-split-files` run leaves exactly ONE top-level module
        # (lean/<Crate>.lean; submodules live under lean/<Crate>/). More than one means the
        # agent ran aeneas twice / in different layouts and left orphans that muddy the analysis.
        top_level = [f for f in info["lean_files"]
                     if f.startswith("lean/") and "/" not in f[len("lean/"):]]
        facts["polluted"] = sorted(top_level) if len(top_level) > 1 else []
        if not info["success"]:
            return facts
        tx = lean.translation_text(self.deps, info["lean_files"])
        facts["axioms"] = lean.external_axioms(tx)
        facts["target_opaqued"] = lean.opaqued_targets(tx, self.target_patterns)
        facts["target_holes"] = lean.target_holes(tx, info["holes"], self.target_patterns)
        build = lean.translation_compiles(self.deps, info["lean_path"])
        facts["compiles"] = bool(build.get("success"))
        facts["build_errors"] = (build.get("stderr", "") or "")[:2000]
        return facts

    def _feedback(self, facts: dict, defects) -> str:
        parts: list[str] = []
        if not facts["success"]:
            return ("No Lean was produced in /workspace/out/lean. Re-run Charon then Aeneas "
                    "(`aeneas -backend lean -dest /workspace/out/lean <llbc>`). If Charon exited "
                    "101, your --start-from pattern did not resolve — fix it from the error "
                    "(use the `crate::` keyword for the package selected by `-p`).")
        if facts["polluted"]:
            parts.append(f"POLLUTED output: multiple top-level Lean modules {facts['polluted']} — you "
                         f"ran aeneas more than once / in different layouts and left orphan files. "
                         f"`rm -rf /workspace/out/lean/*`, then run aeneas ONCE with -split-files so "
                         f"lean/ holds a single crate module (lean/<Crate>.lean + lean/<Crate>/).")
        if facts["target_opaqued"]:
            parts.append(f"MOCK: target function(s) {facts['target_opaqued']} were emitted as "
                         f"`axiom` (opaqued). Translate their BODIES; opaque only their dependencies.")
        if facts["target_holes"]:
            parts.append(f"HOLES: target function(s) {facts['target_holes']} are a bare `sorry`. "
                         f"Opaque a callee or apply a behaviour-preserving refactor so the body "
                         f"translates.")
        if not facts["compiles"]:
            parts.append("The translation does not compile. `lake` errors:\n" + facts["build_errors"])
        for d in (defects or []):
            parts.append(f"JUDGE [{d.kind}]: {d.detail} → fix: {d.fix}")
        return "\n\n".join(parts)

    # ── accept / abort + accountability trail ───────────────────────────────────
    def _accept(self, facts: dict, outcome) -> str:
        info = facts["info"]
        sha = tools.commit(self.deps.container_id, "feat(aeneas): accepted translation", glob="lean/")
        self.deps.progress["aeneas"] = {
            "success": True, "lean_files": info["lean_files"], "lean_path": info["lean_path"],
            "holes": info["holes"], "holes_by_file": info["holes_by_file"], "commit": sha,
        }
        tier = self._record_trail(facts, outcome)
        log.info("TRANSLATE accepted (%s) — %d file(s), %d hole(s), %d assumed axiom(s)",
                 tier, len(info["lean_files"]), len(info["holes"]), len(facts["axioms"]))
        checkpoint.snapshot(self.deps)
        return ""

    def _record_trail(self, facts: dict, outcome) -> str:
        """Build the accountability trail (list of records, compatible with the briefing/report/
        abort-note consumers and the `trail_*` helpers) from the mechanical facts + the agent's
        narrative, and persist it. Returns the weakest tier reached."""
        # Build-config files (managed by setup_lake) are NOT translation modifications — exclude
        # them so patching the lakefile does not falsely downgrade faithfulness to MODIFICATION.
        _BUILD_CFG = {"lakefile.lean", "lean-toolchain", "lake-manifest.json"}
        lean_patched = [f for f in (outcome.lean_files_patched if outcome else [])
                        if f.rsplit("/", 1)[-1] not in _BUILD_CFG]
        edited = facts["repo_files"] + lean_patched
        tier = "MODIFICATION" if edited else ("ASSUMPTION" if facts["axioms"] else "SAFE")
        summary = (outcome.summary if outcome else "").strip()

        trail = [{
            "round": 0, "action": "SCOPE", "tier": "SAFE",
            "scope": {"start_from": self.target_patterns}, "source_edits": [],
            "rationale": summary[:800], "expected_effect": "",
            "result": {"success": True, "target_holes": facts["target_holes"],
                       "holes": facts["holes"], "commit": self.deps.progress["aeneas"]["commit"]},
        }]
        if facts["axioms"]:
            trail.append({
                "round": len(trail), "action": "OPAQUE", "tier": "ASSUMPTION",
                "scope": {"opaque": facts["axioms"],
                          "exclude": list(outcome.excluded_patterns) if outcome else []},
                "source_edits": [],
                "rationale": "trusted leaf dependencies emitted as Lean axioms (assumptions the "
                             "`#print axioms` gate flags on any dependent theorem)",
                "expected_effect": "", "result": {"success": True, "target_holes": [],
                                                   "holes": [], "commit": ""},
            })
        if edited:
            trail.append({
                "round": len(trail), "action": "REFACTOR", "tier": "MODIFICATION", "scope": {},
                "source_edits": [{"path": p, "applied": True,
                                  "justification": "behaviour-preserving (see accountability.md + "
                                                   "translate/source.diff)"} for p in edited],
                "rationale": "behaviour-preserving edit(s); see accountability.md and the git diff",
                "expected_effect": "", "result": {"success": True, "target_holes": [],
                                                   "holes": [], "commit": ""},
            })

        self.deps.progress["translate_trail"] = trail
        tools.write_out(self.deps, "translate/accountability.json", json.dumps(trail, indent=2))
        if facts["repo_diff"]:
            tools.write_out(self.deps, "translate/source.diff", facts["repo_diff"])
        tools.commit(self.deps.container_id, f"translate: accountability trail ({tier})",
                     glob="translate/")
        return tier

    def _abort(self, facts: dict, reason: str) -> None:
        self.deps.progress.setdefault("translate_trail", [{
            "round": 0, "action": "SCOPE", "tier": "SAFE",
            "scope": {"start_from": self.target_patterns}, "source_edits": [],
            "rationale": reason, "expected_effect": "",
            "result": {"success": facts["success"], "target_holes": facts.get("target_holes", []),
                       "holes": facts.get("holes", []), "commit": ""}}])
        detail = []
        if not facts["success"]:
            detail.append("no Lean translation was produced")
        else:
            if facts["target_opaqued"]:
                detail.append(f"target function(s) {facts['target_opaqued']} were opaqued (mocked), "
                              f"not translated")
            if facts["target_holes"]:
                detail.append(f"target function(s) left as holes: {facts['target_holes']}")
            if not facts["compiles"]:
                detail.append("the translation does not compile")
        raise _PipelineAborted(
            "TRANSLATE could not converge: " + reason
            + ((". " + "; ".join(detail)) if detail else "")
            + ". See translate/accountability.md.")


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
        # INFER on the PRISTINE Rust source (before any translation/refactor) — the CODE is the
        # source of truth for behaviour. The design doc is only a FOCUS HINT (which functions/
        # guarantees matter), never a spec to reconcile against. Derives the InformalSpec AND
        # the Charon target patterns (the set of functions the properties concern).
        sources = tools.read_repo_sources(deps)
        entry_functions = deps.progress.get("explore", {}).get("entry_functions", [])
        infer_files = f"### Rust source (pristine, unmodified)\n{sources}"
        if deps.design_doc.strip():
            infer_files += ("\n\n### Design document (FOCUS HINT only — the code, not this doc, "
                            f"is the source of truth)\n{deps.design_doc}")
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
    """Read the spec files needed downstream (informal spec + impl spec) as a formatted block."""
    paths = ["specs/informal_spec.json"]
    if impl := lean.impl_spec(deps):
        paths.append(impl)
    parts = []
    for path in paths:
        content = tools.read_out(deps, path)
        if not content.startswith("ERROR:"):
            parts.append(f"### {path}\n{content}")
    return "\n\n".join(parts)


def _formalise_inputs(deps: AgentDeps) -> str:
    """Static inputs injected into every FORMALISE round: the translated crate + the informal
    spec (the properties, inferred from the code)."""
    block = f"### Aeneas-translated crate\n{lean.translation_text(deps)}\n\n"
    content = tools.read_out(deps, "specs/informal_spec.json")
    if not content.startswith("ERROR:"):
        block += f"### specs/informal_spec.json\n{content}\n\n"
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


class ProvePhase:
    """PROVE against the real build oracle: the agent (via bash) edits proof bodies and runs
    `lake build`, reading the real Lean diagnostics (`unsolved goals` + goal state, type errors)
    to prove goal-directed. The harness owns only the objective guards: a compile gate before any
    effort; a request-limit backstop; restore of the compiling baseline if the agent leaves the
    spec broken; one authoritative final build; and the `#print axioms` gate + implementation-vs-
    abstract partition of the established theorems."""

    def __init__(self, deps: AgentDeps):
        self.deps = deps
        self.impl_spec = lean.impl_spec(deps)

    async def run(self) -> None:
        deps = self.deps
        if "proofs_done" in deps.progress:
            log.info("PROVE already complete — skipping proof stage")
            return
        # Airtight compile gate: NEVER spend proof effort on a spec that does not build (a resumed
        # session may re-enter with a spec that only built in a previous container).
        if not lean.build(deps).get("success"):
            raise _PipelineAborted(
                "PROVE not started — the implementation spec does not compile "
                "(FORMALISE did not produce a spec whose statements build)")
        baseline = tools.read_out(deps, self.impl_spec)   # the compiling (all-sorry) floor

        try:
            await _run_stage(_prove, self._prompt(), deps, "PROVE",
                             request_limit=config.PROVE_REQUEST_LIMIT)
        except UnexpectedModelBehavior as e:
            log.warning("PROVE stage failed after retries: %s", e)
        except UsageLimitExceeded as e:
            log.warning("PROVE hit the request-limit backstop (%s) — finalizing", e)
        checkpoint.snapshot(deps)
        # If the agent left the spec non-compiling, restore the compiling baseline — never finalize
        # on a broken edit; the axiom gate must run on a build that succeeds.
        if not lean.build(deps).get("success"):
            log.warning("PROVE left a non-compiling spec — restoring the compiling baseline")
            tools.write_out(deps, self.impl_spec, baseline)
            lean.build(deps)
        tools.commit(deps.container_id, "stage/prove: proof attempts", glob="lean/")
        if not lean.build(deps).get("success"):
            raise _PipelineAborted("PROVE left the spec in a non-compiling state")
        deps.progress["proofs_done"] = True
        lean.footprint(deps)   # proofs may reference defs the stubs did not
        self._record_axioms()

    def _prompt(self) -> str:
        return (
            f"Proceed to PROVE. Fill in proofs for as many `sorry` theorems in "
            f"`/workspace/out/{self.impl_spec}` as you can, WITHOUT changing any statement. Work ONE "
            f"theorem at a time, easiest first (base cases, concrete values, simple bounds), using "
            f"bash: `grep -n` to locate a theorem, `sed -n` to read its block, an in-place edit to "
            f"replace ONLY its proof body, then `lake build` (from /workspace/out/lean) to check. "
            f"The build shows the REAL errors — an incomplete proof reports `unsolved goals` with "
            f"the remaining goal state. If a proof fails, revert that theorem to `:= by sorry` and "
            f"move on — leaving hard theorems as `sorry` is expected and honest. Keep the file "
            f"compiling; when done, commit with git.")

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

    report_files = _read_spec_files(deps)
    lean_path = deps.progress.get("aeneas", {}).get("lean_path", "")
    if lean_path:
        lean_content = tools.read_out(deps, lean_path)
        if not lean_content.startswith("ERROR:"):
            report_files = f"### {lean_path}\n{lean_content}\n\n{report_files}"
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
        f"dependent on a non-standard axiom (e.g. an assumed/opaqued primitive)."
        f"\n\n{report_files}" + resume_note,
        deps, "REPORT",
    )

    _REPORT_SECTIONS = [
        "report/01_overview.md", "report/02_translation.md",
        "report/03_implementation_spec.md", "report/04_spec_judge.md",
        "report/05_proofs.md", "report/06_summary.md",
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
        ("explore",       "EXPLORE — entry points"),
        ("informal_spec", "INFER — behaviour spec + target scope (pristine source)"),
        ("aeneas",        "TRANSLATE — Aeneas translation (scoped)"),
        ("formal_spec",   "FORMALISE — implementation spec assembled"),
        ("verdict",       "SPEC-JUDGE — statements judged"),
        ("proofs_done",   "PROVE — proofs attempted"),
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
        resume_note = await _run_translate_stages(deps, resume_note)

        if config.STOP_AFTER_TRANSLATE:
            log.info("LUSTERNA_STOP_AFTER_TRANSLATE set — stopping after TRANSLATE so the "
                     "translation artefacts can be inspected (no spec/prove/report).")
            return "Stopped after TRANSLATE (LUSTERNA_STOP_AFTER_TRANSLATE)."

        resume_note = await SpecPhase(deps, resume_note).run()
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
