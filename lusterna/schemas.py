"""Pydantic output schemas shared across pipeline stages and subagents."""
from typing import Literal
from pydantic import BaseModel


# ── DOC stages ────────────────────────────────────────────────────────────────

class AbstractInformalSpec(BaseModel):
    summary: str
    preconditions: list[str]
    postconditions: list[str]
    invariants: list[str]
    edge_cases: list[str]
    open_questions: list[str]


class Ambiguity(BaseModel):
    field: str
    question: str


class AbstractFormalSpec(BaseModel):
    lean_definitions: str
    lean_theorem_stubs: str
    rationale: str
    ambiguities: list[Ambiguity] = []


# ── INFER / FORMALISE stages ──────────────────────────────────────────────────

class InformalSpec(BaseModel):
    summary: str
    preconditions: list[str]
    postconditions: list[str]
    invariants: list[str]
    edge_cases: list[str]


# ── SPEC-JUDGE stage ──────────────────────────────────────────────────────────

class ComponentVerdict(BaseModel):
    name: str
    kind: str
    approved: bool
    score: int
    issues: list[str]
    suggestions: list[str]


class JudgeVerdict(BaseModel):
    approved: bool
    score: int
    issues: list[str]
    suggestions: list[str]
    components: list[ComponentVerdict] = []
    stagnant: bool = False


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


# ── PROVE stage ───────────────────────────────────────────────────────────────

class TheoremProofResult(BaseModel):
    name: str
    kind: str
    status: Literal["proved", "sorry_acceptable", "likely_misstated"]
    proof_attempt: str
    misstatement_reason: str = ""


class ProofVerdict(BaseModel):
    theorems: list[TheoremProofResult]
    stagnant: bool = False
    summary: str


class TheoremEstimate(BaseModel):
    trivial: list[str]
    moderate: list[str]
    hard_acceptable: list[str]
    likely_misstated: list[str]
