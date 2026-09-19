"""The LangGraph multi-agent copilot — §7.1, SPEC-02. **Topology frozen after Phase 3.**

Typed state · a supervisor hub routing to four specialized workers · conditional edges ·
a SQLite checkpointer · structured output at every node boundary.

Phase 4's trace export groups spans by the node names below, Phase 5's audit trail records them as
`actor`, and Phase 6's routing test asserts on them. Renaming one is a cross-phase breaking change
(AGENTS.md §3), not a refactor.

    START → input_guard → ingest → supervisor
    supervisor ─(conditional)→ classifier | coverage | fraud | router | clarify | escalate
    classifier|coverage|fraud → supervisor
    router ─(conditional)→ escalate | finalize
    clarify|escalate|finalize → output_guard → END
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agents import fraud as fraud_agent
from src.agents.classifier import classify
from src.agents.coverage import assess_coverage
from src.agents.router import decide_route, next_action
from src.agents.supervisor import classify_intent, decide_next
from src.config import settings
from src.context.quarantine import quarantine
from src.context.strategies import write_context
from src.guardrails.input_guard import screen_input
from src.guardrails.output_guard import screen_output
from src.mcp_client import MCPToolset
from src.memory.long_term import MemoryRecord, recall, remember
from src.memory.short_term import build_checkpointer
from src.models import ClaimRecord, Intent, RoutingDecision, TriageDecision
from src.observability.audit import emit_audit
from src.observability.tool_logger import current_agent
from src.observability.tracing import claim_span
from src.state import TriageState

log = logging.getLogger(__name__)

WORKER_NODES = ("classifier", "coverage", "fraud")


class TriageGraph:
    """Owns the compiled graph plus the MCP session for the life of a run."""

    def __init__(self, mcp: MCPToolset | None = None) -> None:
        self.mcp = mcp or MCPToolset()
        self._policy_cache: dict[str, dict[str, Any]] = {}
        self.app: Any = None
        self._checkpointer: Any = None

    # ─────────────────────────── MCP-backed lookups ──────────────────────────

    async def _mcp(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Call an MCP tool; an unavailable server degrades to {} rather than raising (NFR-04)."""
        if not self.mcp.available:
            return {}
        try:
            return await self.mcp.call(name, **kwargs) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("MCP %s failed: %s", name, exc)
            return {}

    async def _policy(self, policy_number: str) -> dict[str, Any]:
        if policy_number not in self._policy_cache:
            self._policy_cache[policy_number] = await self._mcp(
                "lookup_policy", policy_number=policy_number
            )
        return self._policy_cache[policy_number]

    # ───────────────────────────────── nodes ─────────────────────────────────

    async def input_guard(self, state: TriageState) -> dict[str, Any]:
        """First node — rules IG-01…IG-05 (src/guardrails/input_guard.py, SPEC-09 §2.1).

        A block never changes the topology: a refusal (IG-02) or a malformed payload (IG-05) goes
        to `escalate`; an out-of-scope servicing request (IG-03) pre-sets the intent, so the
        supervisor sends it to `clarify` without an LLM call. Sanitized narrative text (IG-04)
        replaces the raw description before `ingest` quarantines it.
        """
        current_agent.set("input_guard")
        raw = dict(state.get("raw_input") or {})
        claim_id = str(raw.get("claim_id") or "unknown")
        verdict = await screen_input(state)
        emit_audit(
            actor="input_guard", action="input_screened", decision=verdict, claim_id=claim_id,
            run_id=state.get("run_id"),
            rationale=f"{verdict.action}: {', '.join(verdict.rule_ids) or 'no violations'}",
        )
        for v in verdict.violations:
            emit_audit(
                actor="input_guard",
                action="guardrail_blocked" if verdict.action == "block" else "guardrail_sanitized",
                tool=v["rule_id"], decision=v, rationale=v["detail"], claim_id=claim_id,
                run_id=state.get("run_id"),
            )

        out: dict[str, Any] = {"guard_input": verdict, "step_count": state.get("step_count", 0) + 1}
        if verdict.action == "sanitize" and verdict.sanitized is not None:
            out["raw_input"] = {**raw, "description": verdict.sanitized}
        if verdict.action != "block":
            return out

        hint = verdict.route_hint or "escalate"
        if hint in {"ambiguous", "out_of_scope"}:  # IG-03 → clarify via the supervisor
            out["intent"] = Intent(kind=hint, confidence=1.0,
                                   rationale=f"input guard: {verdict.violations[0]['detail']}")
            return out
        if hint == "other_claimant_data":  # IG-02 → refusal, disclose nothing (AC-06)
            out["intent"] = Intent(kind="other_claimant_data", confidence=1.0,
                                   rationale="input guard IG-02: request names another claimant")
        out["terminal_message"] = (
            "This request cannot be processed. It has been referred to a human handler."
        )
        out["next_step"] = "escalate"
        return out

    async def ingest(self, state: TriageState) -> dict[str, Any]:
        """Validate the claim, quarantine the narrative, recall prior-session context."""
        current_agent.set("ingest")
        raw = dict(state.get("raw_input") or {})
        raw.pop("_fixture", None)  # the oracle never reaches a prompt (SPEC-01 §2.2)

        try:
            claim = ClaimRecord.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            return {
                "errors": [{"component": "ingest", "kind": "schema", "detail": str(exc)}],
                "degraded": True,
                "terminal_message": "The submitted claim could not be read.",
                "step_count": state.get("step_count", 0) + 1,
            }

        qt = quarantine(
            claim.description, source="claimant_description", claim_id=claim.claim_id
        )

        memories = await recall(
            claim.claimant.claimant_id,
            query=f"{claim.line_of_business} loss in {claim.loss_location.city}",
            k=5,
        )
        recalled = [f"{m.kind}: {m.text}" for m in memories]

        await remember(
            claim.claimant.claimant_id,
            MemoryRecord(
                kind="prior_claim",
                text=f"claim {claim.claim_id}, {claim.line_of_business} loss on "
                     f"{claim.loss_date}, reported via {claim.channel}",
                claim_id=claim.claim_id,
                source_run_id=state.get("run_id"),
            ),
        )

        emit_audit(
            actor="ingest", action="claim_ingested", run_id=state.get("run_id"),
            claim_id=claim.claim_id,
            decision={"threats": sorted(qt.threat_kinds), "recalled": len(recalled)},
            rationale="claim validated and narrative quarantined",
        )
        return {
            "claim": claim,
            "untrusted": qt,
            "memory_recall": recalled,
            "step_count": state.get("step_count", 0) + 1,
            "scratchpad": write_context(state, "run", "quarantine_threats", sorted(qt.threat_kinds)),
        }

    async def supervisor(self, state: TriageState) -> dict[str, Any]:
        """The hub. Resolves intent once (the only LLM call here), then sequences deterministically."""
        current_agent.set("supervisor")
        out: dict[str, Any] = {"step_count": state.get("step_count", 0) + 1}

        if out["step_count"] >= settings.max_steps:
            # Loop guard (SPEC-02 §3.1): `decide_next` now routes to escalate; record why, so the
            # decision is visibly degraded rather than a silent referral.
            out["degraded"] = True
            out["errors"] = [{"component": "supervisor", "kind": "step_limit",
                              "detail": f"step limit {settings.max_steps} reached"}]
            emit_audit(actor="supervisor", action="degraded", run_id=state.get("run_id"),
                       decision={"step_count": out["step_count"]},
                       rationale=f"step limit {settings.max_steps} reached — escalating")
            return out

        if state.get("intent") is None and state.get("claim") is not None:
            intent = await classify_intent(state)
            out["intent"] = intent
            if intent.confidence == 0.0 and "model unavailable" in intent.rationale:
                out["degraded"] = True
                out["errors"] = [{"component": "supervisor.intent", "kind": "unavailable",
                                  "detail": intent.rationale}]
            emit_audit(
                actor="supervisor", action="intent_identified", decision=intent,
                run_id=state.get("run_id"),
                claim_id=state["claim"].claim_id, rationale=intent.rationale,
            )
        return out

    async def classifier(self, state: TriageState) -> dict[str, Any]:
        """AC-01 — claim type and severity."""
        current_agent.set("classifier")
        policy = await self._policy(state["claim"].policy_number)
        result = await classify(state, policy=policy)
        if result is None:
            return {
                "errors": [{"component": "classifier", "kind": "unavailable",
                            "detail": "classification model unavailable"}],
                "degraded": True,
                "step_count": state.get("step_count", 0) + 1,
            }
        emit_audit(
            actor="classifier", action="classification_made", decision=result,
            run_id=state.get("run_id"), claim_id=state["claim"].claim_id,
            rationale=f"{result.claim_type}/{result.severity} @ {result.confidence:.2f}",
        )
        return {"classification": result, "step_count": state.get("step_count", 0) + 1}

    async def coverage(self, state: TriageState) -> dict[str, Any]:
        """AC-01 — coverage decision citing the clause applied."""
        current_agent.set("coverage")
        claim = state["claim"]
        policy = await self._policy(claim.policy_number)
        window = await self._mcp(
            "validate_coverage_window",
            policy_number=claim.policy_number,
            loss_date=str(claim.loss_date),
            reported_at=claim.reported_at.isoformat(),
        )
        assessment, retrieved, hops = await assess_coverage(state, policy=policy, window=window)

        emit_audit(
            actor="coverage", action="coverage_decided", decision=assessment,
            run_id=state.get("run_id"), claim_id=claim.claim_id,
            tool="retrieve_policy_clauses",
            rationale=f"{assessment.status} via {assessment.cited_clause or 'no clause'} "
                      f"after {hops} retrieval hop(s)",
        )
        return {
            "coverage": assessment,
            "retrieved": retrieved,
            "rag_hops": hops,
            "degraded": state.get("degraded", False) or assessment.status == "ambiguous",
            "step_count": state.get("step_count", 0) + 1,
            "scratchpad": write_context(state, "run", "coverage_window", window),
        }

    async def fraud(self, state: TriageState) -> dict[str, Any]:
        """AC-02 — fraud-risk signal with the indicators that triggered it."""
        current_agent.set("fraud")
        claim = state["claim"]
        policy = await self._policy(claim.policy_number)
        window = (state.get("scratchpad") or {}).get("run", {}).get("coverage_window") or {}
        history = await self._mcp(
            "check_claim_history", claimant_id=claim.claimant.claimant_id, months=24
        )
        assessment = await fraud_agent.screen_fraud(
            state, policy=policy, window=window, history=history
        )
        emit_audit(
            actor="fraud", action="fraud_assessed", decision=assessment,
            run_id=state.get("run_id"), claim_id=claim.claim_id, tool="check_claim_history",
            rationale=f"risk {assessment.risk} @ {assessment.score:.2f} from "
                      f"{[i.code for i in assessment.indicators]}",
        )
        return {"fraud": assessment, "step_count": state.get("step_count", 0) + 1}

    async def router(self, state: TriageState) -> dict[str, Any]:
        """AC-03 — deterministic queue + escalation. No LLM (design.md §D2)."""
        current_agent.set("router")
        claim = state["claim"]
        decision = decide_route(
            classification=state.get("classification"),
            coverage=state.get("coverage"),
            fraud=state.get("fraud"),
            estimated_amount=claim.estimated_amount,
            degraded=bool(state.get("degraded")),
        )
        emit_audit(
            actor="router", action="routing_decided", decision=decision,
            run_id=state.get("run_id"), claim_id=claim.claim_id, rationale=decision.rationale,
        )
        return {"routing": decision, "step_count": state.get("step_count", 0) + 1}

    # ───────────────────────────── terminal nodes ────────────────────────────

    async def clarify(self, state: TriageState) -> dict[str, Any]:
        """AC-04 — ambiguous or out-of-scope: ask, never mishandle."""
        current_agent.set("clarify")
        intent = state.get("intent")
        kind = intent.kind if intent else "ambiguous"
        messages = {
            "out_of_scope": (
                "This request is not a new loss notification. Policy servicing questions "
                "(renewals, address changes, premium queries) are handled by your servicing team. "
                "If you are reporting a loss, please describe what happened, when, and where."
            ),
            "claim_status": (
                "This looks like a status question about an existing claim rather than a new loss. "
                "Please confirm the claim reference and a handler will respond with its status."
            ),
            "ambiguous": (
                "We could not determine what is being reported. Please describe the loss itself: "
                "what happened, the date it happened, and where. Any other requests will be "
                "handled separately."
            ),
        }
        message = messages.get(kind, messages["ambiguous"])
        emit_audit(
            actor="clarify", action="clarification_requested", run_id=state.get("run_id"),
            claim_id=state["claim"].claim_id if state.get("claim") else None,
            decision={"intent": kind}, rationale="clarification requested rather than triaged",
        )
        return {"terminal_message": message, "step_count": state.get("step_count", 0) + 1}

    async def escalate(self, state: TriageState) -> dict[str, Any]:
        """Human-in-the-loop terminal. Never an approval (AC-03, AC-06)."""
        current_agent.set("escalate")
        intent = state.get("intent")
        routing = state.get("routing")

        if intent is not None and intent.kind == "other_claimant_data":
            # Refuse without confirming or denying that the other claimant exists (AC-06).
            message = (
                "We cannot provide information about any policy or claim other than your own. "
                "Your own loss report has been referred to a claims handler."
            )
            action = "refused"
            rationale = "request for another claimant's data refused"
        elif routing is not None:
            message = next_action(routing)
            action = "escalated"
            rationale = routing.escalation_reason or routing.rationale
        else:
            message = (
                "This claim has been referred to a human claims handler for manual assessment."
            )
            action = "escalated"
            rationale = "escalated before a routing decision could be made"

        emit_audit(
            actor="escalate", action=action, decision={"auto_approved": False},
            run_id=state.get("run_id"),
            claim_id=state["claim"].claim_id if state.get("claim") else None, rationale=rationale,
        )
        return {"terminal_message": message, "step_count": state.get("step_count", 0) + 1}

    async def finalize(self, state: TriageState) -> dict[str, Any]:
        """Non-escalated terminal: the routing decision stands."""
        current_agent.set("finalize")
        routing = state["routing"]
        claim = state["claim"]

        await remember(
            claim.claimant.claimant_id,
            MemoryRecord(
                kind="open_issue",
                text=f"claim {claim.claim_id} routed {routing.queue}; "
                     f"escalation={routing.escalation_required}",
                claim_id=claim.claim_id,
                source_run_id=state.get("run_id"),
            ),
        )
        emit_audit(
            actor="finalize", action="finalized", decision=routing,
            run_id=state.get("run_id"), claim_id=claim.claim_id, rationale=routing.rationale,
        )
        return {"terminal_message": next_action(routing),
                "step_count": state.get("step_count", 0) + 1}

    async def output_guard(self, state: TriageState) -> dict[str, Any]:
        """Last node — rules OG-01…OG-05 (src/guardrails/output_guard.py, SPEC-09 §2.1).

        A block is applied, not just logged: OG-03 (unverifiable clause) downgrades coverage to
        `ambiguous`, and OG-03/OG-04/OG-05 all force `escalation_required=True,
        auto_approved=False` on the decision that leaves the graph.
        """
        current_agent.set("output_guard")
        claim = state.get("claim")
        claim_id = claim.claim_id if claim else str((state.get("raw_input") or {}).get(
            "claim_id") or "unknown")
        verdict = await screen_output(state)
        emit_audit(
            actor="output_guard", action="output_screened", decision=verdict,
            run_id=state.get("run_id"), claim_id=claim_id,
            rationale=f"{verdict.action}: {', '.join(verdict.rule_ids) or 'no violations'}",
        )
        for v in verdict.violations:
            emit_audit(
                actor="output_guard",
                action="guardrail_blocked" if verdict.action == "block" else "guardrail_sanitized",
                tool=v["rule_id"], decision=v, rationale=v["detail"], claim_id=claim_id,
                run_id=state.get("run_id"),
            )

        coverage, routing = state.get("coverage"), state.get("routing")
        classification, fraud = state.get("classification"), state.get("fraud")
        # OG-02: PII scrubbed out of model-written text travels as field replacements.
        coverage = verdict.field_updates.get("coverage", coverage)
        classification = verdict.field_updates.get("classification", classification)
        fraud = verdict.field_updates.get("fraud", fraud)
        if verdict.action == "block":
            if "OG-03" in verdict.rule_ids and coverage is not None:
                coverage = coverage.model_copy(update={
                    "status": "ambiguous",
                    "rationale": f"[OG-03: citation not verifiable] {coverage.rationale}"})
            if routing is not None:
                reason = f"output guard {', '.join(verdict.rule_ids)}"
                routing = RoutingDecision(
                    queue=routing.queue if routing.queue != "fast_track" else "standard",
                    rationale=routing.rationale, escalation_required=True, auto_approved=False,
                    escalation_reason="; ".join(filter(None, [routing.escalation_reason, reason])),
                )
            emit_audit(actor="output_guard", action="escalated", run_id=state.get("run_id"),
                       claim_id=claim_id, decision={"auto_approved": False},
                       rationale=f"blocked by {', '.join(verdict.rule_ids)}")

        decision = TriageDecision(
            claim_id=claim_id,
            run_id=state.get("run_id", "unset"),
            intent=state.get("intent"),
            classification=classification,
            coverage=coverage,
            fraud=fraud,
            routing=routing,
            next_action=state.get("terminal_message", ""),
            message=verdict.sanitized or state.get("terminal_message", ""),
            degraded=bool(state.get("degraded")),
            errors=list(state.get("errors") or []),
            steps=state.get("step_count", 0) + 1,
        )
        return {
            "guard_output": verdict,
            "decision": decision,
            "step_count": state.get("step_count", 0) + 1,
        }

    # ────────────────────────── conditional edges ────────────────────────────

    def route_from_input_guard(self, state: TriageState) -> str:
        return "escalate" if state.get("next_step") == "escalate" else "ingest"

    def route_from_ingest(self, state: TriageState) -> str:
        return "clarify" if state.get("claim") is None else "supervisor"

    def route_from_supervisor(self, state: TriageState) -> str:
        return decide_next(dict(state))

    def route_from_router(self, state: TriageState) -> str:
        routing = state.get("routing")
        return "escalate" if (routing is None or routing.escalation_required) else "finalize"

    # ───────────────────────────────── build ─────────────────────────────────

    def build(self) -> Any:
        g = StateGraph(TriageState)

        g.add_node("input_guard", self.input_guard)
        g.add_node("ingest", self.ingest)
        g.add_node("supervisor", self.supervisor)
        g.add_node("classifier", self.classifier)
        g.add_node("coverage", self.coverage)
        g.add_node("fraud", self.fraud)
        g.add_node("router", self.router)
        g.add_node("clarify", self.clarify)
        g.add_node("escalate", self.escalate)
        g.add_node("finalize", self.finalize)
        g.add_node("output_guard", self.output_guard)

        g.add_edge(START, "input_guard")
        g.add_conditional_edges("input_guard", self.route_from_input_guard,
                                {"ingest": "ingest", "escalate": "escalate"})
        g.add_conditional_edges("ingest", self.route_from_ingest,
                                {"supervisor": "supervisor", "clarify": "clarify"})
        g.add_conditional_edges(
            "supervisor", self.route_from_supervisor,
            {"classifier": "classifier", "coverage": "coverage", "fraud": "fraud",
             "router": "router", "clarify": "clarify", "escalate": "escalate",
             "finalize": "finalize", "supervisor": "supervisor"},
        )
        for worker in WORKER_NODES:
            g.add_edge(worker, "supervisor")  # workers return to the hub
        g.add_conditional_edges("router", self.route_from_router,
                                {"escalate": "escalate", "finalize": "finalize"})
        for terminal in ("clarify", "escalate", "finalize"):
            g.add_edge(terminal, "output_guard")
        g.add_edge("output_guard", END)

        self._checkpointer = build_checkpointer()
        self.app = g.compile(checkpointer=self._checkpointer)
        return self.app


    async def aclose(self) -> None:
        """Close the checkpointer's aiosqlite connection. Its worker thread is non-daemon: left
        open, it keeps the CLI process alive after the run has finished."""
        conn = getattr(self._checkpointer, "conn", None)
        if conn is not None and hasattr(conn, "close"):
            try:
                result = conn.close()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001
                log.warning("checkpointer close failed: %s", exc)


async def build_graph(mcp: MCPToolset | None = None) -> TriageGraph:
    tg = TriageGraph(mcp=mcp)
    tg.build()
    return tg


async def run_claim(
    graph: TriageGraph, raw_claim: dict[str, Any], *, run_id: str, thread_id: str
) -> TriageDecision:
    """Execute one claim end to end. Never raises — a degraded decision beats no decision."""
    from src.state import new_state

    state = new_state(run_id=run_id, thread_id=thread_id, raw_input=raw_claim)
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": settings.max_steps * 2,
    }
    try:
        with claim_span(
            claim_id=str(raw_claim.get("claim_id", "unknown")), thread_id=thread_id,
            scenario=(raw_claim.get("_fixture") or {}).get("scenario"),
        ):
            final = await graph.app.ainvoke(state, config=config)
        decision = final.get("decision")
        if decision is not None:
            return decision
        return _degraded_decision(raw_claim, run_id, "graph produced no decision")
    except Exception as exc:  # noqa: BLE001 - includes GraphRecursionError (SPEC-02 §3.1)
        log.error("run failed: %s: %s", type(exc).__name__, exc)
        emit_audit(
            actor="graph", action="degraded", run_id=run_id,
            decision={"error": type(exc).__name__},
            rationale=f"run terminated early: {exc}",
        )
        return _degraded_decision(raw_claim, run_id, f"{type(exc).__name__}: {exc}")


def _degraded_decision(raw: dict[str, Any], run_id: str, detail: str) -> TriageDecision:
    return TriageDecision(
        claim_id=str(raw.get("claim_id", "unknown")),
        run_id=run_id,
        next_action="Escalate to a human claims handler — the automated triage did not complete.",
        message="Automated triage could not complete. Referred to a human handler.",
        degraded=True,
        errors=[{"component": "graph", "kind": "error", "detail": detail}],
    )
