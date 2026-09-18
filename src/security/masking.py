"""Identifier masking — applied at every chokepoint that writes or prints (NFR-05, Rule R3).

Design note (design.md §D5): masking happens at the *boundary* — tool logger, audit writer, MCP
responses, CLI renderer — so no call site has to remember. Presidio free-text PII detection (Phase 5)
layers on top of these same chokepoints rather than replacing them.

Rule: keep the structural prefix and the last 3 characters; star out the middle. Idempotent.
"""

from __future__ import annotations

import re
from typing import Any

# POL-AU-4471209  ·  CLT-882134  ·  CLM-2026-000117
POLICY_RE = re.compile(r"\bPOL-([A-Z]{2})-([0-9*]{4,})\b")
CLAIMANT_RE = re.compile(r"\bCLT-([0-9*]{4,})\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Phone must be either internationally prefixed (+91-90000-00101) or a bare 10-digit run.
# Deliberately narrow: a looser pattern swallows ISO dates (2026-08-02) and claim ids
# (CLM-2026-000117), destroying facts the agents need and masking values that are not PII.
# Free-text phone numbers in other shapes are Presidio's job in Phase 5, not this layer's.
PHONE_RE = re.compile(r"\+\d[\d\s-]{7,}\d|(?<![\d-])\d{10}(?![\d-])")

SENSITIVE_KEYS = {
    "policy_number", "claimant_id", "email", "phone", "name",
    "claimant_name", "address", "policy", "claimant",
}
KEEP_TAIL = 3


def _star(value: str, keep: int = KEEP_TAIL) -> str:
    if len(value) <= keep:
        return value
    return "*" * (len(value) - keep) + value[-keep:]


def mask_policy_number(value: str) -> str:
    """POL-AU-4471209 -> POL-AU-****209 (idempotent)."""
    return POLICY_RE.sub(lambda m: f"POL-{m.group(1)}-{_star(m.group(2))}", value)


def mask_claimant_id(value: str) -> str:
    """CLT-882134 -> CLT-***134 (idempotent)."""
    return CLAIMANT_RE.sub(lambda m: f"CLT-{_star(m.group(1))}", value)


def mask_email(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        local, _, domain = m.group(0).partition("@")
        return f"{local[0]}{'*' * max(len(local) - 1, 1)}@{domain}"

    return EMAIL_RE.sub(repl, value)


def mask_phone(value: str) -> str:
    return PHONE_RE.sub(lambda m: _star(m.group(0).replace(" ", "")), value)


def mask_text(value: str) -> str:
    """Apply every identifier rule to a free-text string."""
    for fn in (mask_policy_number, mask_claimant_id, mask_email, mask_phone):
        value = fn(value)
    return value


def mask(value: str) -> str:
    """Public single-value entry point. Idempotent."""
    return mask_text(value)


def mask_record(obj: Any, _depth: int = 0) -> Any:
    """Recursively mask a structure before it is logged, printed or returned over MCP.

    Values under a sensitive key are masked whole; every other string still gets the identifier
    regexes applied, because identifiers leak through free text (descriptions, rationales) far more
    often than through the obvious field.
    """
    if _depth > 12:
        return obj
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS and isinstance(v, str):
                out[k] = mask_text(v)
            else:
                out[k] = mask_record(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        seq = [mask_record(v, _depth + 1) for v in obj]
        return type(obj)(seq) if isinstance(obj, tuple) else seq
    return obj


def contains_unmasked_identifier(text: str) -> bool:
    """Verifier hook (SPEC-12 §2.4 check 5): True if a plaintext identifier is present."""
    for m in POLICY_RE.finditer(text):
        if "*" not in m.group(2):
            return True
    for m in CLAIMANT_RE.finditer(text):
        if "*" not in m.group(1):
            return True
    return False
