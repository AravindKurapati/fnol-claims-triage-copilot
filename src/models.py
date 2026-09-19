"""Domain models — SPEC-01 §2.2 / §2.7 and SPEC-02 §2.4.

Every value that crosses a node boundary is a Pydantic model: the §7.1 requirement is "structured
output at node boundaries", and the validators here are where three graded rules are actually
enforced rather than merely prompted:

  * AC-01 — a non-ambiguous coverage assessment MUST cite a clause.
  * AC-03 — an escalated claim can never be auto-approved.
  * AC-02 — a fraud signal MUST carry the indicators that triggered it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

LineOfBusiness = Literal["auto", "property", "liability"]
Severity = Literal["minor", "moderate", "major", "catastrophic"]
CoverageStatus = Literal["covered", "partially_covered", "not_covered", "ambiguous"]
FraudRisk = Literal["low", "medium", "high"]
Queue = Literal["fast_track", "standard", "investigate"]
IntentKind = Literal[
    "fnol_triage", "claim_status", "ambiguous", "out_of_scope", "other_claimant_data"
]
ClauseKind = Literal["coverage", "exclusion", "limit", "procedure", "definition"]


# ─────────────────────────────── input ────────────────────────────────────────


class Claimant(BaseModel):
    claimant_id: str
    name: str
    email: str
    phone: str


class LossLocation(BaseModel):
    city: str
    state: str
    country: str = "IN"


class FixtureExpectations(BaseModel):
    """Generator-supplied oracle. Stripped at ingest; never reaches a prompt (SPEC-01 §2.2)."""

    expected_claim_type: str
    expected_severity: Severity
    expected_coverage_clause: str | None = None
    expected_coverage_status: CoverageStatus = "covered"
    expected_fraud_risk: FraudRisk = "low"
    expected_queue: Queue = "standard"
    expected_escalation: bool = False
    scenario: str


class ClaimRecord(BaseModel):
    claim_id: str
    policy_number: str
    claimant: Claimant
    line_of_business: LineOfBusiness
    reported_at: datetime
    loss_date: date
    loss_location: LossLocation
    estimated_amount: float
    currency: str = "INR"
    description: str = ""
    police_report_filed: bool = False
    injuries_reported: bool = False
    prior_claims_count: int = 0
    channel: Literal["web", "phone", "agent"] = "web"
    attachments: list[str] = Field(default_factory=list)
    fixture: FixtureExpectations | None = Field(default=None, alias="_fixture")

    model_config = {"populate_by_name": True}

    def trusted_facts(self) -> dict[str, object]:
        """Everything a worker may see *except* claimant free text and the fixture oracle.

        `description` is deliberately absent: it is untrusted and only ever reaches a prompt via
        `quarantine.render_for_prompt` (SPEC-03 §2.2).
        """
        return {
            "claim_id": self.claim_id,
            "line_of_business": self.line_of_business,
            "loss_date": self.loss_date.isoformat(),
            "reported_at": self.reported_at.isoformat(),
            "loss_location": self.loss_location.model_dump(),
            "estimated_amount": self.estimated_amount,
            "currency": self.currency,
            "police_report_filed": self.police_report_filed,
            "injuries_reported": self.injuries_reported,
            "prior_claims_count": self.prior_claims_count,
            "channel": self.channel,
            "attachment_count": len(self.attachments),
        }


# ─────────────────────────────── worker outputs ───────────────────────────────


class Intent(BaseModel):
    kind: IntentKind
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    rationale: str = ""


class Classification(BaseModel):
    """AC-01 — claim type and severity."""

    claim_type: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class RetrievedClause(BaseModel):
    clause_id: str
    clause_title: str
    text: str
    kind: ClauseKind = "coverage"
    product: str = ""
    score: float = 0.0


class CoverageAssessment(BaseModel):
    """AC-01 — the coverage decision must cite the clause it applied."""

    status: CoverageStatus
    cited_clause: str | None = None
    clause_text: str | None = None
    applicable_limit: float | None = None
    deductible: float | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def _citation_required(self) -> "CoverageAssessment":
        if self.status != "ambiguous" and not self.cited_clause:
            raise ValueError(
                f"coverage status '{self.status}' requires a cited_clause (AC-01); "
                "only 'ambiguous' may be uncited"
            )
        return self


class FraudIndicator(BaseModel):
    code: str  # FI-01 … FI-12 (SPEC-01 §2.6)
    label: str
    evidence: str
    weight: float = 0.0


class FraudAssessment(BaseModel):
    """AC-02 — the fraud signal must come with the indicators that triggered it."""

    risk: FraudRisk
    score: float = Field(ge=0.0, le=1.0)
    indicators: list[FraudIndicator] = Field(default_factory=list)

    @model_validator(mode="after")
    def _indicators_required_for_risk(self) -> "FraudAssessment":
        if self.risk != "low" and not self.indicators:
            raise ValueError(f"fraud risk '{self.risk}' requires at least one indicator (AC-02)")
        return self


class RoutingDecision(BaseModel):
    """AC-03 — routing with rationale; escalation is never auto-approved."""

    queue: Queue
    rationale: str
    escalation_required: bool = False
    escalation_reason: str | None = None
    auto_approved: bool = False

    @model_validator(mode="after")
    def _no_auto_approval_when_escalated(self) -> "RoutingDecision":
        if self.escalation_required and self.auto_approved:
            raise ValueError(
                "an escalated claim can never be auto-approved (AC-03) — "
                "suspected fraud and high-value claims go to a human"
            )
        if self.escalation_required and not self.escalation_reason:
            raise ValueError("escalation_required=True requires an escalation_reason")
        return self


class GuardVerdict(BaseModel):
    """SPEC-09 §2.1 — a guardrail's decision. Each violation is `{rule_id, severity, detail}`."""

    action: Literal["allow", "sanitize", "block"] = "allow"
    violations: list[dict] = Field(default_factory=list)
    sanitized: str | None = None
    # Where a block sends the request without changing the graph topology:
    # "escalate" (refuse / human) or an intent kind the supervisor routes to `clarify`.
    route_hint: str | None = None
    # Sanitized replacements for structured fields (e.g. OG-02 scrubbing model text that quotes
    # the claimant's narrative), applied by the output_guard node to the outgoing decision.
    field_updates: dict[str, Any] = Field(default_factory=dict)

    @property
    def rule_ids(self) -> list[str]:
        return [v.get("rule_id", "?") for v in self.violations]


class TriageDecision(BaseModel):
    """The structured result the CLI prints and the Phase 6 eval scores."""

    claim_id: str
    run_id: str
    intent: Intent | None = None
    classification: Classification | None = None
    coverage: CoverageAssessment | None = None
    fraud: FraudAssessment | None = None
    routing: RoutingDecision | None = None
    next_action: str = ""
    message: str = ""
    degraded: bool = False
    errors: list[dict] = Field(default_factory=list)
    steps: int = 0
