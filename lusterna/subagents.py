"""Embedded specialists: focused agents called as tools from within pipeline stages.

Unlike pipeline stages (agent.py), these agents are invoked mid-turn via a tool
call, receive no message history, and return structured Pydantic output directly
to the calling stage.  They are invisible to the pipeline loop.
"""
import logging
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import Agent

from . import config, telemetry

log = logging.getLogger(__name__)


# ── output schemas ────────────────────────────────────────────────────────────

# Abstract specs — derived from the design document only, no implementation knowledge.

class AbstractInformalSpec(BaseModel):
    summary: str
    preconditions: list[str]
    postconditions: list[str]
    invariants: list[str]
    edge_cases: list[str]
    open_questions: list[str]   # aspects the design doc does not specify


class Ambiguity(BaseModel):
    field: str      # e.g. "postconditions[0]", "edge_cases"
    question: str   # what needs to be clarified before it can be formalised


class AbstractFormalSpec(BaseModel):
    lean_definitions: str       # abstract type definitions and predicates (no Rust types)
    lean_theorem_stubs: str     # theorem statements with sorry, no implementation knowledge
    rationale: str
    ambiguities: list[Ambiguity] = []   # questions for the doc-inferrer; empty when converged


# Implementation specs — derived from the Aeneas-translated Lean code.

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


class Discrepancy(BaseModel):
    abstract_component: str   # name/description from abstract spec, or "N/A" if absent there
    impl_component: str       # name/description from impl spec, or "N/A" if absent there
    kind: Literal[
        "implementation_wrong",  # Rust code diverges from the design intent — CRITICAL
        "bridge_wrong",          # impl spec was mis-derived from the translation — CRITICAL
        "abstract_wrong",        # abstract model misreads the design document
        "design_doc_silent",     # design doc simply does not cover this aspect — acceptable gap
    ]
    severity: Literal["critical", "minor", "gap"]
    description: str          # precise explanation of what differs and why it matters


class RefinementObligation(BaseModel):
    name: str        # proposed Lean theorem name, e.g. "fib_impl_refines_abstract"
    statement: str   # full Lean 4 theorem statement (with sorry proof placeholder)
    rationale: str   # why this bridge is needed


class ReconciliationReport(BaseModel):
    aligned: list[str]                          # impl components that satisfy the abstract spec
    discrepancies: list[Discrepancy]
    refinement_obligations: list[RefinementObligation]
    summary: str


class JudgeVerdict(BaseModel):
    approved: bool
    score: int                      # 0-10 overall
    issues: list[str]               # overall / cross-cutting issues
    suggestions: list[str]          # overall suggestions
    components: list[ComponentVerdict] = []   # per-theorem/definition breakdown
    stagnant: bool = False          # True only when the formaliser is genuinely stuck
                                    # (see judge instructions for the precise criterion)


# ── helpers ───────────────────────────────────────────────────────────────────

def _agent(output_type, instructions: str, model: str | None = None) -> Agent:
    m = model or config.MODEL
    return Agent(m, output_type=output_type,
                 model_settings=config.cache_settings(m),
                 instructions=instructions)


async def _run(agent: Agent, prompt: str):
    result = await agent.run(prompt)
    telemetry.session.record(result.usage)
    return result.output


# ── subagent definitions ──────────────────────────────────────────────────────

_doc_inferrer = _agent(
    AbstractInformalSpec,
    "You are a formal methods expert. Given ONLY a design document (no source code), "
    "infer the abstract specification of the system: what it should do according to the "
    "design intent, its preconditions, postconditions, invariants, and edge cases. "
    "Where the design document is silent or ambiguous, record the gap in open_questions. "
    "Do NOT invent behaviour not evidenced by the design document. "
    "Be implementation-independent: do not assume any particular data representation "
    "or algorithm.",
)

_doc_formaliser = _agent(
    AbstractFormalSpec,
    "You are a Lean 4 expert. Given an abstract informal specification derived from a "
    "design document (no implementation knowledge), produce a formal specification as "
    "Lean 4 definitions and theorem stubs. "
    "Use abstract mathematical types (Nat, List, Set, etc.) — never mention Rust types, "
    "UInt64, or any implementation detail. Each theorem stub must carry a docstring. "
    "If any aspect of the informal spec is too ambiguous to formalise faithfully, record "
    "it in ambiguities so the doc-inferrer can resolve it. "
    "Leave ambiguities empty when the spec is clear enough to formalise.",
)

_spec_inferrer = _agent(
    InformalSpec,
    "You are a formal methods expert. Given Lean 4 code translated from Rust "
    "and an optional design document, infer the informal specification of the program: "
    "what it should do, its preconditions, postconditions, invariants, and edge cases. "
    "Be precise and concise. Do not invent behaviour not evidenced by the code or doc.",
)

_formal_spec_writer = _agent(
    FormalSpec,
    "You are a Lean 4 expert. Given an informal specification, produce a formal specification "
    "as Lean 4 definitions and theorem stubs (no sorry-free proofs required yet). "
    "Use idiomatic Lean 4 / Mathlib style. Each theorem stub must have a docstring "
    "explaining what it captures.",
)

_judge = _agent(
    JudgeVerdict,
    "You are a rigorous reviewer of formal specifications. "
    "Evaluate whether the provided formal Lean 4 specification faithfully captures "
    "the informal specification and the original Rust source. "
    "Score 0-10. Flag any gaps, unsound definitions, or missing invariants.",
    model=config.JUDGE_MODEL,
)

_summariser = _agent(
    str,
    "Summarise the following agent conversation into a concise technical paragraph. "
    "Preserve all file paths, Lean theorem names, lake build errors, git commit SHAs, "
    "and any decisions made. Focus on what was attempted, what succeeded, and what failed.",
)


# ── public async entry points ─────────────────────────────────────────────────

async def infer_abstract_informal_spec(design_doc: str) -> AbstractInformalSpec:
    log.info("Doc-inferrer: deriving abstract informal spec from design document")
    return await _run(_doc_inferrer, f"### Design document\n{design_doc}")


async def derive_abstract_formal_spec(abstract_informal: AbstractInformalSpec) -> AbstractFormalSpec:
    log.info("Doc-formaliser: deriving abstract formal spec")
    return await _run(_doc_formaliser,
                      f"### Abstract informal specification\n{abstract_informal.model_dump_json(indent=2)}")


async def refine_abstract_informal_spec(
    design_doc: str,
    current: AbstractInformalSpec,
    ambiguities: list[Ambiguity],
) -> AbstractInformalSpec:
    log.info("Doc-inferrer: refining abstract informal spec to resolve %d ambiguity/ies", len(ambiguities))
    ambiguity_lines = "\n".join(f"  - {a.field}: {a.question}" for a in ambiguities)
    prompt = (
        f"### Design document\n{design_doc}\n\n"
        f"### Current abstract informal specification\n{current.model_dump_json(indent=2)}\n\n"
        f"### Ambiguities to resolve\n{ambiguity_lines}\n\n"
        "Revise the abstract informal specification to resolve these ambiguities using only "
        "the design document as evidence. Where the design document cannot resolve an "
        "ambiguity, record it in open_questions."
    )
    return await _run(_doc_inferrer, prompt)


async def infer_informal_spec(lean_code: str, design_doc: str) -> InformalSpec:
    log.info("Subagent: inferring informal specification")
    return await _run(_spec_inferrer,
                      f"### Lean 4 code\n{lean_code}\n\n### Design document\n{design_doc}")


async def derive_formal_spec(informal: InformalSpec, lean_code: str) -> FormalSpec:
    log.info("Subagent: deriving formal specification")
    prompt = (
        f"### Informal specification\n{informal.model_dump_json(indent=2)}\n\n"
        f"### Lean 4 translated code\n{lean_code}"
    )
    return await _run(_formal_spec_writer, prompt)


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
    return await _run(_judge, prompt)


# ── context compaction ────────────────────────────────────────────────────────

def _messages_to_text(messages: list) -> str:
    import json
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
        return json.dumps(data, indent=2)
    except Exception:
        return str(messages)


async def compact(messages: list) -> list:
    """Summarise *messages* and return a replacement single-message list.

    The summariser has no hooks, so it cannot trigger compaction recursively.
    Falls back to the original list if summarisation fails.
    """
    try:
        summary = await _run(_summariser, _messages_to_text(messages))
    except Exception as exc:
        log.warning("Compaction summariser failed (%s) — keeping messages as-is", exc)
        return messages

    from pydantic_ai.messages import ModelRequest, UserPromptPart
    log.info("Compaction: %d messages → 1 summary message", len(messages))
    return [ModelRequest(parts=[UserPromptPart(
        content=f"[COMPACTED CONTEXT — earlier conversation summary]\n{summary}"
    )])]
