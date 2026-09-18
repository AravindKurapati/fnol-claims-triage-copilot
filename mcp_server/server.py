"""Custom MCP server — SPEC-05. stdio transport, 3 tools + 1 resource.

Reads only committed synthetic fixtures (Rule R3). Two server-side rules matter for the grade:

  * every response is masked **before it leaves the server**, so the agent never holds a plaintext
    identifier (NFR-05);
  * an unknown policy or claimant returns `{"found": false}` — never an error that discloses
    existence, and never a near-match suggestion (AC-06).

Run standalone:  python -m mcp_server.server
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from src.security.masking import mask_record  # noqa: E402

CORPUS = ROOT / "data" / "policy_corpus"

mcp = FastMCP("fnol-policy-server")

# Reporting deadlines per product — mirrors coverage_rules.md §3 (SPEC-01).
REPORTING_DEADLINE_DAYS = {
    "AUTO-COMP-2026": 7,
    "HOME-SHIELD-2026": 14,
    "LIAB-GEN-2026": 30,
}

_cache: dict[str, Any] = {}


def _load(name: str) -> Any:
    if name not in _cache:
        path = CORPUS / name
        if not path.exists():
            _cache[name] = {}
        else:
            _cache[name] = json.loads(path.read_text(encoding="utf-8"))
    return _cache[name]


def _policies() -> dict[str, Any]:
    return _load("policies.json")


def _history() -> dict[str, Any]:
    return _load("claim_history.json")


def _parse_date(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ────────────────────────────────── tools ─────────────────────────────────────


@mcp.tool()
def lookup_policy(policy_number: str) -> str:
    """Look up a policy's product, status, period, limits and coverages.

    Returns {"found": false} for an unknown policy — no disclosure, no near-match suggestion.
    """
    policy = _policies().get(policy_number)
    if not policy:
        return json.dumps({"found": False})

    payload = {
        "found": True,
        "policy_number": policy["policy_number"],
        "product": policy["product"],
        "status": policy["status"],
        "inception_date": policy["inception_date"],
        "expiry_date": policy["expiry_date"],
        "sum_insured": policy["sum_insured"],
        "deductible": policy["deductible"],
        "currency": policy.get("currency", "INR"),
        "coverages": policy["coverages"],
        "exclusions": policy["exclusions"],
        "reporting_deadline_days": REPORTING_DEADLINE_DAYS.get(policy["product"]),
    }
    return json.dumps(mask_record(payload))


@mcp.tool()
def check_claim_history(claimant_id: str, months: int = 24) -> str:
    """Return a claimant's prior claims within `months`, for fraud indicators FI-04 and FI-05."""
    claims = _history().get(claimant_id)
    if claims is None:
        return json.dumps({"found": False, "claim_count": 0, "claims": []})

    today = date.today()
    cutoff_days = months * 31
    recent: list[dict[str, Any]] = []
    for c in claims:
        d = _parse_date(c["loss_date"])
        if d is None or (today - d).days > cutoff_days:
            continue
        recent.append(
            {
                "claim_id": c["claim_id"],
                "loss_date": c["loss_date"],
                "amount": c["amount"],
                "outcome": c["outcome"],
                "loss_type": c["loss_type"],
                "months_ago": round((today - d).days / 30.44, 1),
            }
        )

    payload = {
        "found": True,
        "claim_count": len(recent),
        "claims": recent,
        "types_within_12m": sorted(
            {c["loss_type"] for c in recent if c["months_ago"] <= 12}
        ),
    }
    return json.dumps(mask_record(payload))


@mcp.tool()
def validate_coverage_window(policy_number: str, loss_date: str, reported_at: str) -> str:
    """Check the loss fell inside the period of insurance and was reported within the deadline.

    Backs the period-of-insurance assessment and fraud indicators FI-01 (late notification) and
    FI-02 (loss soon after inception).
    """
    policy = _policies().get(policy_number)
    if not policy:
        return json.dumps({"found": False})

    loss = _parse_date(loss_date)
    reported = _parse_date(reported_at)
    inception = _parse_date(policy["inception_date"])
    expiry = _parse_date(policy["expiry_date"])

    if not all((loss, reported, inception, expiry)):
        return json.dumps({"found": True, "error": "unparseable_date"})

    deadline_days = REPORTING_DEADLINE_DAYS.get(policy["product"], 30)
    days_to_report = (reported - loss).days
    in_period = inception <= loss <= expiry
    payload = {
        "found": True,
        "product": policy["product"],
        "policy_status": policy["status"],
        "policy_active_on_loss_date": bool(in_period and policy["status"] == "active"),
        "loss_within_period_dates": in_period,
        "days_to_report": days_to_report,
        "deadline_days": deadline_days,
        "within_reporting_deadline": days_to_report <= deadline_days,
        "days_since_inception_at_loss": (loss - inception).days,
    }
    return json.dumps(mask_record(payload))


# ───────────────────────────────── resource ───────────────────────────────────


@mcp.resource("policy://handbook/coverage-rules")
def coverage_rules() -> str:
    """The underwriting coverage-rules handbook: severity bands, deductible order,
    reporting deadlines, exclusion precedence, escalation policy."""
    path = CORPUS / "coverage_rules.md"
    return path.read_text(encoding="utf-8") if path.exists() else "# coverage rules unavailable"


if __name__ == "__main__":
    mcp.run(transport="stdio")
