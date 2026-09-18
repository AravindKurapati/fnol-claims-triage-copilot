"""Fraud indicator catalog FI-01 … FI-12 — SPEC-01 §2.6.

Split by design (design.md §D2): the eight deterministic indicators are computed in code from claim
facts and MCP lookups; the four narrative indicators are the only ones the LLM assesses. Risk
banding is arithmetic on the weights, never a model opinion — a model that can be talked into
"fraud risk: low" by the narrative it is reading would defeat AC-02 and AC-03 at once.

Every indicator carries its `evidence`, because AC-02 requires the indicators that triggered the
signal, not just the signal.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from src.config import settings
from src.models import ClaimRecord, FraudAssessment, FraudIndicator

# code -> (label, weight, detector-kind)
CATALOG: dict[str, tuple[str, float, str]] = {
    "FI-01": ("Reported after the policy's reporting deadline", 0.18, "deterministic"),
    "FI-02": ("Loss occurred soon after policy inception", 0.16, "deterministic"),
    "FI-03": ("Estimated amount is a suspiciously round number", 0.06, "deterministic"),
    "FI-04": ("Three or more prior claims in 24 months", 0.16, "deterministic"),
    "FI-05": ("Prior claim of the same loss type within 12 months", 0.14, "deterministic"),
    "FI-06": ("Estimated amount is at or near the sum insured", 0.18, "deterministic"),
    "FI-07": ("Theft or burglary reported without a police report", 0.20, "deterministic"),
    "FI-08": ("No supporting documentation attached to the claim", 0.08, "deterministic"),
    "FI-09": ("Narrative is internally inconsistent", 0.14, "llm"),
    "FI-10": ("Narrative uses coached or templated phrasing", 0.10, "llm"),
    "FI-11": ("A key detail is unverifiable or conspicuously vague", 0.10, "llm"),
    "FI-12": ("Narrative pressures for immediate settlement", 0.10, "llm"),
}

LLM_CODES = [c for c, (_, _, kind) in CATALOG.items() if kind == "llm"]
DETERMINISTIC_CODES = [c for c, (_, _, kind) in CATALOG.items() if kind == "deterministic"]

INCEPTION_WINDOW_DAYS = 30
NEAR_LIMIT_RATIO = 0.85
THEFT_TYPES = {"theft", "burglary", "housebreaking", "malicious_damage"}


def make(code: str, evidence: str) -> FraudIndicator:
    label, weight, _ = CATALOG[code]
    return FraudIndicator(code=code, label=label, evidence=evidence, weight=weight)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def detect_deterministic(
    claim: ClaimRecord,
    *,
    policy: dict[str, Any] | None = None,
    window: dict[str, Any] | None = None,
    history: dict[str, Any] | None = None,
    claim_type: str | None = None,
) -> list[FraudIndicator]:
    """Compute FI-01…FI-08 from claim facts plus MCP lookups. No LLM involved."""
    out: list[FraudIndicator] = []
    policy = policy or {}
    window = window or {}
    history = history or {}
    loss_type = (claim_type or "").lower()

    # FI-01 — late notification
    if window.get("within_reporting_deadline") is False:
        out.append(make(
            "FI-01",
            f"reported {window.get('days_to_report')} days after loss; "
            f"policy deadline is {window.get('deadline_days')} days",
        ))

    # FI-02 — loss soon after inception
    days_since = window.get("days_since_inception_at_loss")
    if isinstance(days_since, int) and 0 <= days_since <= INCEPTION_WINDOW_DAYS:
        out.append(make("FI-02", f"loss occurred {days_since} days after policy inception"))

    # FI-03 — round number
    amount = claim.estimated_amount
    if amount >= 50_000 and amount % 50_000 == 0:
        out.append(make("FI-03", f"estimated amount {amount:,.0f} is an exact multiple of 50,000"))

    # FI-04 — claim frequency
    count = history.get("claim_count", claim.prior_claims_count)
    if isinstance(count, int) and count >= 3:
        out.append(make("FI-04", f"{count} prior claims in the last 24 months"))

    # FI-05 — same loss type recently
    types_12m = {t.lower() for t in (history.get("types_within_12m") or [])}
    if loss_type and loss_type in types_12m:
        out.append(make("FI-05", f"a prior '{loss_type}' claim was made within the last 12 months"))

    # FI-06 — at or near the sum insured
    sum_insured = policy.get("sum_insured")
    if isinstance(sum_insured, (int, float)) and sum_insured > 0:
        ratio = amount / sum_insured
        if ratio >= NEAR_LIMIT_RATIO:
            out.append(make(
                "FI-06",
                f"estimated amount is {ratio:.0%} of the sum insured ({sum_insured:,.0f})",
            ))

    # FI-07 — theft/burglary without a police report
    if loss_type in THEFT_TYPES and not claim.police_report_filed:
        out.append(make("FI-07", f"'{loss_type}' claim filed with no police report"))

    # FI-08 — no documentation
    if not claim.attachments:
        out.append(make("FI-08", "no photographs, estimates or supporting documents attached"))

    return out


def score(indicators: list[FraudIndicator]) -> float:
    """Additive weights, capped at 1.0. Deliberately simple and auditable."""
    return min(round(sum(i.weight for i in indicators), 4), 1.0)


def band(value: float) -> str:
    if value >= settings.fraud_high_score:
        return "high"
    if value >= settings.fraud_medium_score:
        return "medium"
    return "low"


def assess(indicators: list[FraudIndicator]) -> FraudAssessment:
    """Build the AC-02 assessment: banded risk with the indicators that produced it."""
    deduped: dict[str, FraudIndicator] = {}
    for ind in indicators:
        if ind.code in CATALOG and ind.code not in deduped:
            deduped[ind.code] = ind
    ordered = [deduped[c] for c in CATALOG if c in deduped]
    value = score(ordered)
    return FraudAssessment(risk=band(value), score=value, indicators=ordered)
