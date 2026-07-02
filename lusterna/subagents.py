"""Specialist subagents spawned by the orchestrator for focused tasks."""
import logging
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import Agent

from . import config

log = logging.getLogger(__name__)


# ── output schemas ────────────────────────────────────────────────────────────

class InformalSpec(BaseModel):
    summary: str
    preconditions: list[str]
    postconditions: list[str]
    invariants: list[str]
    edge_cases: list[str]


class FormalSpec(BaseModel):
    lean_definitions: str    # Lean 4 type definitions and predicates
    lean_theorem_stubs: str  # theorem statements (without proofs)
    rationale: str


class ComponentVerdict(BaseModel):
    name: str                # e.g. "fib_recursive_correct"
    kind: str                # "theorem" | "definition" | "lemma" | "other"
    approved: bool
    score: int               # 0-10
    issues: list[str]        # specific problems with this component
    suggestions: list[str]   # actionable fixes


class TheoremProofResult(BaseModel):
    name: str
    kind: str                                          # "theorem" | "lemma" | "definition"
    status: Literal["proved", "sorry_acceptable", "likely_misstated"]
    proof_attempt: str                                 # what was tried / what proof was found
    misstatement_reason: str = ""                      # precise logical reason; only for likely_misstated


class ProofVerdict(BaseModel):
    theorems: list[TheoremProofResult]
    stagnant: bool = False                             # see proof-judge instructions for criterion
    summary: str                                       # brief overall narrative


class JudgeVerdict(BaseModel):
    approved: bool
    score: int                      # 0-10 overall
    issues: list[str]               # overall / cross-cutting issues
    suggestions: list[str]          # overall suggestions
    components: list[ComponentVerdict] = []   # per-theorem/definition breakdown
    stagnant: bool = False          # True only when the formaliser is genuinely stuck
                                    # (see judge instructions for the precise criterion)


# ── subagent definitions ──────────────────────────────────────────────────────

_spec_inferrer = Agent(
    config.MODEL,
    output_type=InformalSpec,
    instructions=(
        "You are a formal methods expert. Given Lean 4 code translated from Rust "
        "and an optional design document, infer the informal specification of the program: "
        "what it should do, its preconditions, postconditions, invariants, and edge cases. "
        "Be precise and concise. Do not invent behaviour not evidenced by the code or doc."
    ),
)

_formal_spec_writer = Agent(
    config.MODEL,
    output_type=FormalSpec,
    instructions=(
        "You are a Lean 4 expert. Given an informal specification, produce a formal specification "
        "as Lean 4 definitions and theorem stubs (no sorry-free proofs required yet). "
        "Use idiomatic Lean 4 / Mathlib style. Each theorem stub must have a docstring "
        "explaining what it captures."
    ),
)

_judge = Agent(
    config.JUDGE_MODEL,
    output_type=JudgeVerdict,
    instructions=(
        "You are a rigorous reviewer of formal specifications. "
        "Evaluate whether the provided formal Lean 4 specification faithfully captures "
        "the informal specification and the original Rust source. "
        "Score 0-10. Flag any gaps, unsound definitions, or missing invariants."
    ),
)

_summariser = Agent(
    config.MODEL,
    output_type=str,
    instructions=(
        "Summarise the following agent conversation history into a concise paragraph "
        "capturing all decisions made, artefacts produced, and open questions. "
        "Preserve technical details such as file paths and commit SHAs."
    ),
)


# ── public async entry points ─────────────────────────────────────────────────

async def infer_informal_spec(lean_code: str, design_doc: str) -> InformalSpec:
    log.info("Subagent: inferring informal specification")
    prompt = f"### Lean 4 code\n{lean_code}\n\n### Design document\n{design_doc}"
    result = await _spec_inferrer.run(prompt)
    return result.output


async def derive_formal_spec(informal: InformalSpec, lean_code: str) -> FormalSpec:
    log.info("Subagent: deriving formal specification")
    prompt = (
        f"### Informal specification\n{informal.model_dump_json(indent=2)}\n\n"
        f"### Lean 4 translated code\n{lean_code}"
    )
    result = await _formal_spec_writer.run(prompt)
    return result.output


async def judge_formal_spec(
    informal: InformalSpec,
    formal: FormalSpec,
    rust_source: str,
    build_result: dict,
) -> JudgeVerdict:
    log.info("Subagent: judging formal specification")
    build_section = (
        "### lake build result\n"
        f"success: {build_result.get('success', False)}\n"
        f"stdout: {build_result.get('stdout', '')[:800]}\n"
        f"stderr: {build_result.get('stderr', '')[:800]}\n"
    )
    prompt = (
        f"### Informal spec\n{informal.model_dump_json(indent=2)}\n\n"
        f"### Formal spec\n{formal.model_dump_json(indent=2)}\n\n"
        f"### Original Rust source\n{rust_source}\n\n"
        f"{build_section}\n"
        "IMPORTANT: if lake build failed, approved MUST be false and score MUST be ≤ 4. "
        "A spec that does not type-check cannot be approved."
    )
    result = await _judge.run(prompt)
    return result.output


async def summarise_history(history_text: str) -> str:
    log.info("Subagent: summarising context history")
    result = await _summariser.run(history_text)
    return result.output
