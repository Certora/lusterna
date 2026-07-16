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


# ── EXPLORE stage ────────────────────────────────────────────────────────────

class ExploreResult(BaseModel):
    entry_file: str
    entry_functions: list[str]


# ── DOC stages ────────────────────────────────────────────────────────────────

class AbstractInformalSpec(BaseModel):
    summary: str
    preconditions: list[str]
    postconditions: list[str]
    invariants: list[str]
    edge_cases: list[str]
    open_questions: list[str]


class AbstractFormalSpec(BaseModel):
    lean_definitions: str
    lean_theorem_stubs: str
    rationale: str


# ── INFER / FORMALISE stages ──────────────────────────────────────────────────

class InformalSpec(BaseModel):
    summary: str
    preconditions: list[str]
    postconditions: list[str]
    invariants: list[str]
    edge_cases: list[str]
    # Charon name-matcher patterns naming the concrete crate items that make up the
    # verification target (e.g. "crate::fib", or "crate::MyType" for a type + all its
    # methods). Derived by INFER from the abstract spec + the behaviour it inferred from
    # the pristine source; TRANSLATE uses them as `--start-from` scope. Empty ⇒ whole crate.
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


# ── TRANSLATE remediation stage ───────────────────────────────────────────────

class SourceEdit(BaseModel):
    """One behaviour-preserving edit to a Rust source file, proposed by the TRANSLATE
    remediation agent and applied by the orchestrator (never by the agent directly)."""
    path: str      # repo-relative, e.g. "src/lib.rs"
    find: str      # exact anchor snippet; must occur EXACTLY ONCE in the file
    replace: str   # replacement text ("" to delete the anchor)
    behavior_preservation_justification: str  # why this does NOT change observable behaviour


class RemediationAction(BaseModel):
    """The next remediation step the TRANSLATE loop should take when Charon/Aeneas fail
    or leave holes in the target's call-closure. Escalation order (least invasive first):
    opaque (axiomatize an in-closure dependency) → refactor (behaviour-preserving source
    edit) → give_up."""
    tier: Literal["opaque", "refactor", "give_up"]
    opaque: list[str] = Field(default_factory=list)   # tier=opaque: Charon patterns to axiomatize
    exclude: list[str] = Field(default_factory=list)  # optional: drop strictly out-of-closure items
    include: list[str] = Field(default_factory=list)  # optional: whitelist refinement
    source_edits: list[SourceEdit] = Field(default_factory=list)  # tier=refactor only
    rationale: str
    expected_effect: str   # e.g. "removes hole crate.parse from the target closure"


# ── SPEC-JUDGE stage ──────────────────────────────────────────────────────────

class SpecDefect(BaseModel):
    theorem: str   # Lean identifier, or "coverage" for a missing property
    kind: Literal["vacuous", "too_weak", "wrong_statement",
                  "missing_coverage", "over_specified"]
    detail: str    # the specific reason it is a defect
    fix: str       # concrete change FORMALISE should make


class JudgeVerdict(BaseModel):
    defects: list[SpecDefect]   # empty == sound spec; approval is derived in Python


# ── RECONCILE stage ───────────────────────────────────────────────────────────

class Discrepancy(BaseModel):
    abstract_component: str
    impl_component: str
    kind: Literal[
        "implementation_wrong",
        "bridge_wrong",
        "abstract_wrong",
        "design_doc_silent",
    ]
    severity: Literal["critical", "minor", "gap"]
    description: str


class RefinementObligation(BaseModel):
    name: str
    statement: str
    rationale: str


class ReconciliationReport(BaseModel):
    aligned: list[str]
    discrepancies: list[Discrepancy]
    refinement_obligations: list[RefinementObligation]
    summary: str
