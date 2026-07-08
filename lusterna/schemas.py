"""Pydantic output schemas for the structured-output pipeline stages."""
from typing import Literal
from pydantic import BaseModel


# ── EXPLORE stage ────────────────────────────────────────────────────────────

class ExploreResult(BaseModel):
    entry_file: str
    entry_functions: list[str]
    aeneas_incompatibilities: list[str]
    suggested_rust_changes: list[str]


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
