"""Data types for the pipeline: the shared dependency object plus the pydantic output
schemas for the structured-output stages."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── shared dependency object (injected into every agent tool via RunContext) ─────

@dataclass
class AgentDeps:
    container_id: str
    repo_path: Path        # host path — used only for initial docker cp push
    work_path: Path        # host path — used only for final docker cp pull
    session_id: str
    design_doc: str
    progress: dict[str, Any] = field(default_factory=dict)
    message_history: list = field(default_factory=list)
    # Set True only when the pipeline runs fully to completion (through REPORT). Drives container
    # lifecycle: an incomplete run (budget hit, interrupt, crash) keeps its container ALIVE so a
    # resume can re-attach with full state (repo edits/shims, out/, accountability baseline).
    completed: bool = False


# ── EXPLORE stage ────────────────────────────────────────────────────────────

class ToolchainAssessment(BaseModel):
    """What EXPLORE discovered EMPIRICALLY by running Charon/Aeneas on the target — an ADVISORY
    input to INFER and TRANSLATE, never authoritative: TRANSLATE, the TRANSLATE-JUDGE, and the
    `#print axioms` gate still decide correctness. Everything here is a discovered fact or a
    proposed strategy, not a constraint the downstream stages must obey."""
    buildable: bool = False
    # build-environment fixes found (and applied) to get an llbc — NOT modifications to the
    # program under analysis (e.g. "drop cdylib from solana-program [lib]", "neutralise ahash
    # feature(stdsimd)", "update vendored .cargo-checksum after edit").
    build_prereqs: list[str] = Field(default_factory=list)
    # external crates/modules the target only USES and need not be verified → opaque (the trust
    # boundary): frameworks, oracles, external-protocol account/amount types.
    opaque_boundary: list[str] = Field(default_factory=list)
    # types/values whose exact semantics a property needs but which Aeneas cannot translate, so
    # they must be MODELLED (e.g. "Fraction (fixed::U68F60) → scaled-int rational", "U256 → Nat").
    must_model: list[str] = Field(default_factory=list)
    # constructs/types the coarse Aeneas pass errored or holed on.
    translatability_walls: list[str] = Field(default_factory=list)
    notes: str = ""   # short narrative of what the probe found


class ExploreResult(BaseModel):
    entry_file: str
    entry_functions: list[str]
    assessment: ToolchainAssessment = Field(default_factory=ToolchainAssessment)


# ── INFER / FORMALISE stages ──────────────────────────────────────────────────

class InformalSpec(BaseModel):
    summary: str
    preconditions: list[str]
    postconditions: list[str]
    invariants: list[str]
    edge_cases: list[str]
    # Charon name-matcher patterns naming the FUNCTIONS/METHODS under verification (the set
    # the properties concern), e.g. "crate::m::my_fn" or "crate::m::_::method". Derived by
    # INFER from the pristine code (doc as focus hint); TRANSLATE scopes to them via
    # `--start-from` and guarantees they are translated, never opaqued. Empty ⇒ whole crate.
    target_patterns: list[str] = Field(default_factory=list)


class TheoremStub(BaseModel):
    name: str        # a valid Lean identifier
    signature: str   # binders + " : " + proposition — everything BEFORE `:=`; NO proof


class FormalSpec(BaseModel):
    """Structured FORMALISE output. A proof is deliberately NOT a field: the orchestrator
    assembles each theorem as `theorem <name> <signature> := by sorry`, so FORMALISE
    structurally cannot write proofs (that is PROVE's job)."""
    preamble: str                # imports, opens, and any helper `def`s — no theorems
    theorems: list[TheoremStub]


# ── TRANSLATE stage ───────────────────────────────────────────────────────────

class TranslateOutcome(BaseModel):
    """The TRANSLATE agent's self-report of what it did, returned after it has driven
    Charon/Aeneas at the shell. The facts (source diff, emitted axioms, compilation) are
    re-derived mechanically by the harness; this narrative is for the accountability trail,
    the human reviewer, and the TRANSLATE-JUDGE."""
    gave_up: bool = False               # the target is intrinsically untranslatable
    summary: str                        # what was done and WHY (the human-review narrative)
    opaque_patterns: list[str] = Field(default_factory=list)   # Charon --opaque patterns settled on
    excluded_patterns: list[str] = Field(default_factory=list)  # Charon --exclude patterns settled on
    source_files_edited: list[str] = Field(default_factory=list)   # Rust files edited (repo-relative)
    lean_files_patched: list[str] = Field(default_factory=list)    # post-extraction Lean files edited


class TranslateDefect(BaseModel):
    kind: Literal["target_mocked", "holes_in_target", "over_opaqued",
                  "semantics_changed", "not_faithful", "non_compiling", "other"]
    detail: str   # the specific problem
    fix: str      # concrete change the TRANSLATE agent should make


class TranslateVerdict(BaseModel):
    """SPEC-JUDGE analogue for TRANSLATE: the semantic gate. An empty defect list approves
    the translation (it faithfully mirrors the original, any edits are behaviour-preserving,
    and the target is genuinely translated — not mocked)."""
    defects: list[TranslateDefect]   # empty == accept


# ── SPEC-JUDGE stage ──────────────────────────────────────────────────────────

class SpecDefect(BaseModel):
    theorem: str   # Lean identifier, or "coverage" for a missing property
    kind: Literal["vacuous", "too_weak", "wrong_statement",
                  "missing_coverage", "over_specified"]
    detail: str    # the specific reason it is a defect
    fix: str       # concrete change FORMALISE should make


class JudgeVerdict(BaseModel):
    defects: list[SpecDefect]   # empty == sound spec; approval is derived in Python
