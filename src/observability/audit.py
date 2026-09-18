"""Audit trail of consequential agent actions — AC-10.

PHASE 5 SEAM. `emit_audit()` is already called from every consequential decision point in the graph
(SPEC-09 §2.3). The writer below is functional so the trail exists from Phase 2 onward; Phase 5 adds
guardrail violation records and the Presidio pass, without touching a single call site.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.security.masking import mask_record

AUDIT_LOG = "agent_actions.jsonl"

CONSEQUENTIAL_ACTIONS = {
    "input_screened",
    "intent_identified",
    "claim_ingested",
    "classification_made",
    "coverage_decided",
    "fraud_assessed",
    "routing_decided",
    "escalated",
    "finalized",
    "clarification_requested",
    "output_screened",
    "guardrail_blocked",
    "refused",
    "degraded",
}


def emit_audit(
    *,
    actor: str,
    action: str,
    decision: Any = None,
    tool: str | None = None,
    rationale: str = "",
    claim_id: str | None = None,
    run_id: str | None = None,
    **extra: Any,
) -> None:
    """Append one audit record. Append-only JSONL, masked (NFR-05)."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    if hasattr(decision, "model_dump"):
        decision = decision.model_dump(mode="json")

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "actor": actor,
        "action": action,
        "tool": tool,
        "decision": decision,
        "rationale": rationale,
        "claim_id": claim_id,
        **extra,
    }
    with (settings.logs_dir / AUDIT_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(mask_record(record), default=str) + "\n")
