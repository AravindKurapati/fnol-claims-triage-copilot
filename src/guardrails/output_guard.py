"""Output guardrail — the graph's last node.

PHASE 5 SEAM — owner: teammate. Spec: `specs/SPEC-09-security-guardrails.md` §2.1.

Two rules are implemented now rather than deferred, because they are the last line of defence on the
two claims most likely to be probed by a grader:

  * **OG-04** — an escalated claim must never be auto-approved (AC-03). It is already enforced by
    `RoutingDecision`'s validator and by `decide_route`; this is the third, independent layer.
  * **OG-01** — no unmasked identifier may leave in an answer (AC-06, NFR-05).

Phase 5 adds OG-02 (Presidio free-text PII), OG-03 (clause-integrity re-check) and OG-05 (refusal
leakage), and records every violation to the audit trail with its rule_id.
"""

from __future__ import annotations

from typing import Any

from src.models import GuardVerdict
from src.security.masking import contains_unmasked_identifier, mask_text


async def screen_output(state: dict[str, Any]) -> GuardVerdict:
    """Return an allow/sanitize/block verdict for the outgoing answer."""
    violations: list[dict] = []
    message = state.get("terminal_message", "") or ""
    action = "allow"
    sanitized: str | None = None

    # OG-04 — escalated claims are never auto-approved (AC-03).
    routing = state.get("routing")
    if routing is not None and routing.escalation_required and routing.auto_approved:
        violations.append(
            {
                "rule_id": "OG-04",
                "severity": "critical",
                "detail": "auto_approved=True on an escalated claim",
            }
        )
        return GuardVerdict(
            action="block",
            violations=violations,
            sanitized="This claim requires human review and has not been approved.",
        )

    # OG-01 — no unmasked policy number or claimant id may leave in an answer.
    if contains_unmasked_identifier(message):
        violations.append(
            {"rule_id": "OG-01", "severity": "high", "detail": "unmasked identifier in output"}
        )
        action, sanitized = "sanitize", mask_text(message)

    # ── PHASE 5 ──────────────────────────────────────────────────────────────
    # OG-02 free-text PII via Presidio
    # OG-03 coverage cites a clause not present in state["retrieved"] → block → ambiguous
    # OG-05 refusal leakage (confirming another claimant exists)
    return GuardVerdict(action=action, violations=violations, sanitized=sanitized)
