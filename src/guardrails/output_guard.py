"""Output guardrail — the graph's last node (SPEC-09 §2.1). Rules OG-01 … OG-05.

| Rule  | Detects                                                   | Action                          |
|-------|-----------------------------------------------------------|---------------------------------|
| OG-01 | unmasked policy number / claimant id in the answer        | sanitize (mask) — AC-06         |
| OG-02 | free-text PII in model-written text quoting the narrative | sanitize via Presidio           |
| OG-03 | coverage cites a clause that was never retrieved          | **block** → ambiguous → human   |
| OG-04 | `auto_approved` on an escalated claim                     | **block** — AC-03 last defence  |
| OG-05 | a refusal that confirms another claimant/policy exists    | **block** → generic refusal     |

OG-04 is the third independent layer behind `decide_route` and `RoutingDecision`'s validator. OG-03
closes the gap a hallucinated citation would otherwise slip through: the coverage agent's own
integrity retry (SPEC-06 §2.4) checks the model's pick, this checks the *final* state.
The node (src/graph.py::output_guard) applies the verdict and writes each violation to the audit
trail with its rule_id.
"""

from __future__ import annotations

import re
from typing import Any

from src.guardrails.pii import redact
from src.models import GuardVerdict
from src.security.masking import contains_unmasked_identifier, mask_text

GENERIC_REFUSAL = (
    "We cannot provide information about any policy or claim other than your own. "
    "Your own loss report has been referred to a claims handler."
)
HUMAN_REVIEW = "This claim requires human review and has not been approved."
_IDENT = re.compile(r"\b(?:CLT-[0-9*]{4,}|POL-[A-Z]{2}-[0-9*]{4,})\b")
_LEAK_WORDS = re.compile(
    r"\b(neighbou?r'?s?|his|her|their)\s+(claim|policy)\b.{0,60}\b(is|was|shows|has|exists?)\b|"
    r"\b(found|located|exists)\b.{0,40}\b(claim|policy)\b",
    re.IGNORECASE,
)


def _v(rule_id: str, severity: str, detail: str) -> dict[str, str]:
    return {"rule_id": rule_id, "severity": severity, "detail": detail}


def check_auto_approval(state: dict[str, Any]) -> list[dict[str, str]]:
    """OG-04 — an escalated claim is never auto-approved (AC-03)."""
    routing = state.get("routing")
    if routing is not None and routing.escalation_required and routing.auto_approved:
        return [_v("OG-04", "critical", "auto_approved=True on an escalated claim")]
    return []


def check_citation_integrity(state: dict[str, Any]) -> list[dict[str, str]]:
    """OG-03 — the cited clause must be one the RAG tool actually returned in this run."""
    coverage = state.get("coverage")
    if coverage is None or not coverage.cited_clause:
        return []
    retrieved = {c.clause_id for c in (state.get("retrieved") or [])}
    if coverage.cited_clause not in retrieved:
        return [_v("OG-03", "high",
                   f"cited clause {coverage.cited_clause} not in the {len(retrieved)} "
                   "retrieved clause(s)")]
    return []


def check_refusal_leak(state: dict[str, Any], message: str) -> list[dict[str, str]]:
    """OG-05 — a cross-claimant refusal must not confirm or reveal anything about the other party."""
    intent = state.get("intent")
    guard_in = state.get("guard_input")
    refusing = (intent is not None and intent.kind == "other_claimant_data") or (
        guard_in is not None and "IG-02" in guard_in.rule_ids
    )
    if not refusing:
        return []
    claim = state.get("claim")
    own = set()
    if claim is not None:
        own = {mask_text(claim.claimant.claimant_id), mask_text(claim.policy_number),
               claim.claimant.claimant_id, claim.policy_number}
    foreign = [m for m in _IDENT.findall(message) if m not in own]
    if foreign or _LEAK_WORDS.search(message):
        return [_v("OG-05", "high", "refusal message references another claimant's data")]
    return []


async def screen_output(state: dict[str, Any]) -> GuardVerdict:
    """Return an allow / sanitize / block verdict for the outgoing answer."""
    message = state.get("terminal_message", "") or ""

    violations = check_auto_approval(state)
    if violations:
        return GuardVerdict(action="block", violations=violations, sanitized=HUMAN_REVIEW,
                            route_hint="escalate")

    violations = check_refusal_leak(state, message)
    if violations:
        return GuardVerdict(action="block", violations=violations, sanitized=GENERIC_REFUSAL,
                            route_hint="escalate")

    violations = check_citation_integrity(state)
    if violations:
        return GuardVerdict(
            action="block", violations=violations, route_hint="escalate",
            sanitized="The coverage position could not be verified against the policy wording. "
                      "This claim has been referred to a human claims handler.",
        )

    action, sanitized = "allow", None
    if contains_unmasked_identifier(message):
        violations.append(_v("OG-01", "high", "unmasked identifier in output"))
        action, sanitized = "sanitize", mask_text(message)

    updates, entities = scrub_model_text(state)
    if updates:
        violations.append(_v("OG-02", "medium",
                             f"free-text PII redacted from {', '.join(sorted(updates))}: "
                             f"{', '.join(sorted(entities))}"))
        action = "sanitize"

    return GuardVerdict(action=action, violations=violations, sanitized=sanitized,
                        field_updates=updates)


def scrub_model_text(state: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """OG-02 — Presidio over the model-written fields that quote the claimant's narrative.

    The terminal message is deliberately *not* scanned: it is a system template filled from
    structured values, and the small NER model tagged its word "Queue" as a PERSON, rewriting every
    escalation instruction to "<PERSON>: standard" (docs/failure-analysis.md F-07). The fields that
    can actually carry a third party's name or number are the evidence quotes and rationales.
    """
    updates: dict[str, Any] = {}
    entities: set[str] = set()

    def clean(text: str) -> str:
        result = redact(text)
        entities.update(f["entity"] for f in result.findings)
        return result.text

    cls = state.get("classification")
    if cls is not None and cls.evidence:
        scrubbed = [clean(e) for e in cls.evidence]
        if scrubbed != cls.evidence:
            updates["classification"] = cls.model_copy(update={"evidence": scrubbed})
    cov = state.get("coverage")
    if cov is not None and cov.rationale:
        scrubbed_r = clean(cov.rationale)
        if scrubbed_r != cov.rationale:
            updates["coverage"] = cov.model_copy(update={"rationale": scrubbed_r})
    fraud = state.get("fraud")
    if fraud is not None and fraud.indicators:
        inds = [i.model_copy(update={"evidence": clean(i.evidence)}) for i in fraud.indicators]
        if [i.evidence for i in inds] != [i.evidence for i in fraud.indicators]:
            updates["fraud"] = fraud.model_copy(update={"indicators": inds})
    return updates, entities
