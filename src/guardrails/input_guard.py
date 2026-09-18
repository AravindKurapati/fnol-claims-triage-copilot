"""Input guardrail — the graph's first node.

PHASE 5 SEAM — owner: teammate. Spec: `specs/SPEC-09-security-guardrails.md` §2.1.

The node exists and is wired in from Phase 2, so Phase 5 fills validators without touching the graph
topology (which the Phase 4 trace export and Phase 6 tests reconcile against).

Phase 3 behaviour below is deliberately minimal and non-blocking: threat *detection* already happens
in `src/context/quarantine.py`, and the claim still gets triaged — a narrative carrying an injection
payload is real evidence about a real loss. Phase 5 adds the blocking rules IG-01…IG-05 using
Guardrails-AI / LLM Guard validators.
"""

from __future__ import annotations

from typing import Any

from src.models import GuardVerdict


async def screen_input(state: dict[str, Any]) -> GuardVerdict:
    """Return an allow/sanitize/block verdict for the incoming request."""
    violations: list[dict] = []

    raw = state.get("raw_input") or {}
    description = str(raw.get("description", ""))

    # IG-05 — oversized payload. Cheap, deterministic, and safe to enforce now.
    if len(description) > 20_000:
        violations.append(
            {"rule_id": "IG-05", "severity": "medium", "detail": "narrative exceeds 20,000 chars"}
        )
        return GuardVerdict(action="block", violations=violations)

    # ── PHASE 5 ──────────────────────────────────────────────────────────────
    # IG-01 prompt injection            → sanitize (quarantine already annotates; add LLM Guard)
    # IG-02 cross-claimant data request → block + escalate, disclose nothing
    # IG-03 out-of-scope request        → block → clarify
    # IG-04 PII in the wrong field      → sanitize via Presidio
    # Each violation must carry its rule_id — docs/risk-register.md cites these as the control.
    return GuardVerdict(action="allow", violations=violations)
