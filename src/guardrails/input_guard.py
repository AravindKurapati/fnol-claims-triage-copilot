"""Input guardrail — the graph's first node (SPEC-09 §2.1). Rules IG-01 … IG-05.

Explicit, deterministic policy functions (the brief allows "Guardrails-AI / LLM Guard validators, or
policy functions") plus Presidio for free-text PII. Deterministic on purpose: a guard that a model
can talk its way past is not a control, and `tests/` can assert every rule without an API key.

| Rule  | Detects                                   | Action                                        |
|-------|-------------------------------------------|-----------------------------------------------|
| IG-01 | prompt injection / instruction override   | sanitize — narrative stays quarantined, flag  |
| IG-02 | request for another claimant's data       | **block** → escalate as a refusal (AC-06)     |
| IG-03 | out-of-scope servicing request, no loss   | **block** triage → clarify (AC-04)            |
| IG-04 | free-text PII in the narrative            | sanitize via Presidio                         |
| IG-05 | oversized / malformed payload             | **block** → human                             |

The verdict never changes the graph topology: a block that must refuse routes to `escalate`, and a
block that must clarify pre-sets the intent so the supervisor sends it to `clarify`.
Every violation is written to `logs/agent_actions.jsonl` by the node (src/graph.py::input_guard) with
its rule_id — the ids `docs/risk-register.md` cites as controls.
"""

from __future__ import annotations

import re
from typing import Any

from src.context.threats import detect_threats, names_foreign_claimant
from src.guardrails.pii import redact
from src.models import GuardVerdict

MAX_NARRATIVE_CHARS = 20_000
REQUIRED_FIELDS = ("claim_id", "policy_number", "claimant", "line_of_business", "loss_date")

INJECTION_KINDS = {"instruction_injection", "role_manipulation", "output_manipulation",
                   "delimiter_escape"}

# Servicing asks that are not a First Notice of Loss (AC-04).
_SERVICING = re.compile(
    r"\b(renew(al|s)?|premium|add\s+.{0,30}address|change\s+(of\s+)?address|new\s+address|"
    r"quote|cancel\s+(my\s+)?policy|no[\s-]claim\s+bonus)\b",
    re.IGNORECASE,
)
# Evidence that a concrete loss is actually being reported.
_LOSS = re.compile(
    r"\b(damage[sd]?|dented|broke(n)?|crack(ed)?|stolen|theft|burgl|fire|flood(ed)?|leak(ed|ing)?|"
    r"collid|crash|hit|smash(ed)?|tore|injur(ed|y)|water\s+came)\b",
    re.IGNORECASE,
)


def _v(rule_id: str, severity: str, detail: str) -> dict[str, str]:
    return {"rule_id": rule_id, "severity": severity, "detail": detail}


def check_payload(raw: dict[str, Any]) -> list[dict[str, str]]:
    """IG-05 — oversized or malformed input."""
    description = raw.get("description", "")
    if not isinstance(description, str):
        return [_v("IG-05", "medium", "narrative is not a string")]
    if len(description) > MAX_NARRATIVE_CHARS:
        return [_v("IG-05", "medium", f"narrative exceeds {MAX_NARRATIVE_CHARS:,} chars")]
    missing = [f for f in REQUIRED_FIELDS if not raw.get(f)]
    if missing:
        return [_v("IG-05", "medium", f"missing required field(s): {', '.join(missing)}")]
    return []


def check_cross_claimant(raw: dict[str, Any]) -> list[dict[str, str]]:
    """IG-02 — the narrative asks about a claimant or policy that is not the filer's."""
    description = str(raw.get("description", ""))
    own_claimant = str((raw.get("claimant") or {}).get("claimant_id", ""))
    own_policy = str(raw.get("policy_number", ""))
    if names_foreign_claimant(description, own_claimant):
        return [_v("IG-02", "high", "narrative requests data about another claimant id")]
    for m in re.finditer(r"\bPOL-[A-Z]{2}-\d{4,}\b", description):
        if m.group(0) != own_policy:
            return [_v("IG-02", "high", "narrative references another policy number")]
    return []


def check_injection(raw: dict[str, Any]) -> list[dict[str, str]]:
    """IG-01 — instruction-override payloads. Sanitize, never obey: the text stays quarantined."""
    kinds = sorted({t.kind for t in detect_threats(str(raw.get("description", "")))} &
                   INJECTION_KINDS)
    if kinds:
        return [_v("IG-01", "high", f"injection pattern(s) in narrative: {', '.join(kinds)}")]
    return []


def check_out_of_scope(raw: dict[str, Any]) -> tuple[list[dict[str, str]], str | None]:
    """IG-03 — servicing request with no quantified loss. Returns (violations, preset intent)."""
    description = str(raw.get("description", ""))
    if not _SERVICING.search(description):
        return [], None
    try:
        amount = float(raw.get("estimated_amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount > 0:
        return [], None  # a quantified loss is triaged; the servicing ask is handled separately
    intent = "ambiguous" if _LOSS.search(description) else "out_of_scope"
    return [_v("IG-03", "low", f"servicing request without a quantified loss → {intent}")], intent


async def screen_input(state: dict[str, Any]) -> GuardVerdict:
    """Return an allow / sanitize / block verdict for the incoming FNOL."""
    raw = state.get("raw_input") or {}

    violations = check_payload(raw)
    if violations:
        return GuardVerdict(action="block", violations=violations, route_hint="escalate")

    violations = check_cross_claimant(raw)
    if violations:
        return GuardVerdict(action="block", violations=violations,
                            route_hint="other_claimant_data")

    oos, preset_intent = check_out_of_scope(raw)
    if oos:
        return GuardVerdict(action="block", violations=oos, route_hint=preset_intent)

    violations = check_injection(raw)
    action = "sanitize" if violations else "allow"

    pii = redact(str(raw.get("description", "")))
    if pii.found:
        entities = sorted({f["entity"] for f in pii.findings})
        violations.append(_v("IG-04", "medium",
                             f"free-text PII redacted ({pii.engine}): {', '.join(entities)}"))
        return GuardVerdict(action="sanitize", violations=violations, sanitized=pii.text)

    return GuardVerdict(action=action, violations=violations)
