"""Routing and escalation — AC-03. **A pure function. No LLM participates.**

AC-03 says suspected-fraud and high-value claims are escalated to a human, *not auto-approved*. A
prompt instruction is not a control, so the rule lives here as arithmetic over the workers'
structured outputs, and is defended three times over (design.md §D2):

  1. this function computes it;
  2. `RoutingDecision` refuses to construct an escalated-and-auto-approved object (src/models.py);
  3. Phase 5's output guard `OG-04` blocks it at the graph's exit.

`tests/test_routing.py` (Phase 6) asserts against this function directly, which is why it takes
plain models rather than the graph state.
"""

from __future__ import annotations

from src.config import settings
from src.models import Classification, CoverageAssessment, FraudAssessment, RoutingDecision

FAST_TRACK_SEVERITIES = {"minor", "moderate"}


def decide_route(
    *,
    classification: Classification | None,
    coverage: CoverageAssessment | None,
    fraud: FraudAssessment | None,
    estimated_amount: float,
    degraded: bool = False,
) -> RoutingDecision:
    """Deterministic queue + escalation decision (SPEC-02 §2.5)."""
    fraud_risk = fraud.risk if fraud else "high"          # absent assessment is not "low"
    indicator_count = len(fraud.indicators) if fraud else 0
    coverage_status = coverage.status if coverage else "ambiguous"
    severity = classification.severity if classification else "major"

    reasons: list[str] = []

    # ── queue ────────────────────────────────────────────────────────────────
    if fraud_risk == "high" or indicator_count >= settings.investigate_min_indicators:
        queue = "investigate"
        reasons.append(
            f"fraud risk {fraud_risk} with {indicator_count} indicator(s) "
            f"(>= {settings.investigate_min_indicators} triggers investigation)"
            if indicator_count >= settings.investigate_min_indicators
            else f"fraud risk {fraud_risk}"
        )
    elif (
        fraud_risk == "low"
        and coverage_status == "covered"
        and estimated_amount < settings.fast_track_max_amount
        and severity in FAST_TRACK_SEVERITIES
    ):
        queue = "fast_track"
        reasons.append(
            f"low fraud risk, coverage confirmed, {severity} severity, "
            f"value {estimated_amount:,.0f} below the fast-track ceiling "
            f"{settings.fast_track_max_amount:,.0f}"
        )
    else:
        queue = "standard"
        reasons.append(
            f"fraud risk {fraud_risk}, coverage {coverage_status}, severity {severity}, "
            f"value {estimated_amount:,.0f}"
        )

    # ── escalation — orthogonal to queue (AC-03) ─────────────────────────────
    escalation_reasons: list[str] = []
    if fraud_risk == "high":
        escalation_reasons.append("suspected fraud (high fraud risk)")
    if estimated_amount >= settings.high_value_threshold:
        escalation_reasons.append(
            f"high-value claim ({estimated_amount:,.0f} >= "
            f"{settings.high_value_threshold:,.0f})"
        )
    if coverage_status == "ambiguous":
        escalation_reasons.append("coverage could not be determined")
    if degraded:
        escalation_reasons.append("run degraded — a component was unavailable")

    escalation_required = bool(escalation_reasons)

    return RoutingDecision(
        queue=queue,
        rationale="; ".join(reasons),
        escalation_required=escalation_required,
        escalation_reason="; ".join(escalation_reasons) if escalation_required else None,
        # An escalated claim is a recommendation for a human. Never auto-approved.
        auto_approved=(not escalation_required) and queue == "fast_track",
    )


def next_action(decision: RoutingDecision) -> str:
    """The human-facing instruction that accompanies the decision."""
    if decision.escalation_required:
        return (
            f"Escalate to a human claims handler — {decision.escalation_reason}. "
            f"Queue: {decision.queue}. This is a recommendation, not an approval."
        )
    if decision.queue == "fast_track":
        return "Auto-approve for fast-track settlement by a junior handler."
    if decision.queue == "investigate":
        return "Refer to the Special Investigation Unit before any assessment."
    return "Assign to a claims handler for full desk assessment."
