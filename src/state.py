"""The typed graph state — SPEC-01 §2.7. **Frozen after Phase 3.**

Phases 4–6 reconcile their evidence against these field names: the trace export groups by them, the
audit trail records them, and the routing tests construct them directly. Renaming a field here is a
cross-phase breaking change, not a refactor (AGENTS.md §3).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from src.context.quarantine import QuarantinedText
from src.models import (
    Classification,
    ClaimRecord,
    CoverageAssessment,
    FraudAssessment,
    GuardVerdict,
    Intent,
    RetrievedClause,
    RoutingDecision,
    TriageDecision,
)


class TriageState(TypedDict, total=False):
    # --- identity: every artifact and every doc citation keys on these ---
    run_id: str
    thread_id: str

    # --- input ---
    raw_input: dict[str, Any]
    claim: ClaimRecord | None
    untrusted: QuarantinedText | None

    # --- worker outputs (structured at every node boundary) ---
    intent: Intent | None
    classification: Classification | None
    coverage: CoverageAssessment | None
    fraud: FraudAssessment | None
    routing: RoutingDecision | None
    decision: TriageDecision | None

    # --- working context ---
    retrieved: Annotated[list[RetrievedClause], operator.add]
    memory_recall: list[str]
    scratchpad: dict[str, Any]
    messages: Annotated[list[AnyMessage], add_messages]

    # --- control ---
    next_step: str | None
    step_count: int
    rag_hops: int
    errors: Annotated[list[dict], operator.add]
    degraded: bool
    terminal_message: str

    # --- guardrails (Phase 5 fills the verdicts; the fields exist from Phase 2) ---
    guard_input: GuardVerdict | None
    guard_output: GuardVerdict | None


def new_state(
    *, run_id: str, thread_id: str, raw_input: dict[str, Any]
) -> TriageState:
    return TriageState(
        run_id=run_id,
        thread_id=thread_id,
        raw_input=raw_input,
        claim=None,
        untrusted=None,
        intent=None,
        classification=None,
        coverage=None,
        fraud=None,
        routing=None,
        decision=None,
        retrieved=[],
        memory_recall=[],
        scratchpad={},
        messages=[],
        next_step=None,
        step_count=0,
        rag_hops=0,
        errors=[],
        degraded=False,
        terminal_message="",
        guard_input=None,
        guard_output=None,
    )
