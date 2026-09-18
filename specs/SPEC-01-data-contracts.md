# SPEC-01 — Data Contracts & Synthetic Data

**Phase 1** · Satisfies: Rule R3 (Synthetic-Data), NFR-05, and supplies the inputs for NFR-02 / Rule R5.

---

## 1. Purpose & graded requirement

Define every data shape the system passes around, and generate the **committed synthetic inputs** the whole
submission is reproduced from. Rule R3: only synthetic claims and policy documents we generate; policy
numbers and claimant identifiers must be synthetic **and masked where shown**, and **never written to logs
in plaintext**.

## 2. Contract

### 2.1 Identifier formats

| Entity | Format | Example | Masked form |
|---|---|---|---|
| Claim | `CLM-<YYYY>-<6 digits>` | `CLM-2026-000117` | not masked (not PII) |
| Policy | `POL-<LOB2>-<7 digits>` | `POL-AU-4471209` | `POL-AU-****209` |
| Claimant | `CLT-<6 digits>` | `CLT-882134` | `CLT-***134` |
| Policy product | `<LOB>-<PLAN>-<YEAR>` | `AUTO-COMP-2026` | not masked |
| Clause | `<product> §<major>.<minor>` | `AUTO-COMP-2026 §4.2` | not masked |
| Run | `run-<uuid4 hex[:12]>` | `run-9f2c1ab77d04` | not masked |

**Masking rule** (`src/security/masking.py`): keep the structural prefix and the **last 3 characters**;
replace the rest of the variable part with `*`. Masking is idempotent — masking a masked value is a no-op.

### 2.2 FNOL claim record — `data/sample_claims/*.json`

```jsonc
{
  "claim_id": "CLM-2026-000117",
  "policy_number": "POL-AU-4471209",
  "claimant": {
    "claimant_id": "CLT-882134",
    "name": "Anita Raghavan",          // synthetic
    "email": "anita.r@example.invalid",// .invalid TLD — never routable
    "phone": "+91-90000-00117"
  },
  "line_of_business": "auto",           // auto | property | liability
  "reported_at": "2026-09-14T10:22:00Z",
  "loss_date": "2026-09-12",
  "loss_location": { "city": "Pune", "state": "MH", "country": "IN" },
  "estimated_amount": 185000.0,
  "currency": "INR",
  "description": "<free text — UNTRUSTED>",
  "police_report_filed": false,
  "injuries_reported": false,
  "prior_claims_count": 1,
  "channel": "web",                      // web | phone | agent
  "attachments": ["photo_front.jpg"],
  "_fixture": {                          // generator metadata; never fed to the model
    "expected_claim_type": "collision",
    "expected_severity": "moderate",
    "expected_coverage_clause": "AUTO-COMP-2026 §4.2",
    "expected_fraud_risk": "low",
    "expected_queue": "standard",
    "expected_escalation": false,
    "scenario": "clean_standard"
  }
}
```

`_fixture` is the **eval oracle** (Phase 6 golden set) and the routing-test source (Phase 6). It is stripped
by `ingest` and never reaches a prompt.

### 2.3 Policy master — `data/policy_corpus/policies.json`

```jsonc
{
  "POL-AU-4471209": {
    "policy_number": "POL-AU-4471209",
    "product": "AUTO-COMP-2026",
    "claimant_id": "CLT-882134",
    "status": "active",                  // active | lapsed | cancelled
    "inception_date": "2026-01-15",
    "expiry_date": "2027-01-14",
    "sum_insured": 900000.0,
    "deductible": 5000.0,
    "currency": "INR",
    "coverages": ["collision", "theft", "glass", "third_party_liability"],
    "exclusions": ["racing", "unlicensed_driver", "wear_and_tear"],
    "endorsements": []
  }
}
```

### 2.4 Policy documents — `data/policy_corpus/<PRODUCT>.md`

Markdown, one file per product, with **numbered clauses in a fixed heading form** so the RAG chunker can
attach a stable `clause_id`:

```markdown
# AUTO-COMP-2026 — Comprehensive Motor Policy

## §4. Own Damage Cover

### §4.2 Collision
The insurer shall indemnify the insured for loss or damage to the insured vehicle caused by
accidental collision ... subject to the deductible stated in the schedule.
**Limit:** sum insured.  **Deductible:** as per schedule.  **Waiting period:** none.
```

Chunker contract: heading regex `^#{2,3}\s+§(\d+(?:\.\d+)?)\s+(.+)$` → `clause_id = f"{product} §{num}"`.
Every chunk carries metadata `{product, clause_id, clause_title, section, path}`.

Required corpus coverage: **3 products** (`AUTO-COMP-2026`, `HOME-SHIELD-2026`, `LIAB-GEN-2026`), each with
an own-damage/peril section, an exclusions section, a limits & deductibles section, a claims-procedure
section including **reporting deadlines** (fraud indicator FI-01 depends on this), and ≥ 10 clauses.

### 2.5 Claim history fixture — `data/policy_corpus/claim_history.json`

```jsonc
{ "CLT-882134": [ { "claim_id": "CLM-2025-000042", "loss_date": "2025-03-04",
                    "amount": 42000.0, "outcome": "settled", "loss_type": "glass" } ] }
```

Backs the MCP `check_claim_history` tool (SPEC-05) and fraud indicators FI-04 / FI-05.

### 2.6 Fraud indicator catalog — `src/agents/fraud_indicators.py`

| Code | Indicator | Detector |
|---|---|---|
| FI-01 | Reported after the policy's reporting deadline | deterministic |
| FI-02 | Loss within N days of policy inception | deterministic |
| FI-03 | Estimated amount is a suspiciously round number | deterministic |
| FI-04 | ≥ 3 prior claims in 24 months | deterministic |
| FI-05 | Prior claim of the same loss type within 12 months | deterministic |
| FI-06 | Amount ≥ 85% of sum insured | deterministic |
| FI-07 | Theft/burglary with no police report | deterministic |
| FI-08 | Loss date is a weekend/holiday **and** no third-party witness | deterministic |
| FI-09 | Narrative internally inconsistent (dates, sequence, actors) | LLM |
| FI-10 | Narrative uses coached / templated phrasing | LLM |
| FI-11 | Key detail unverifiable or conspicuously vague | LLM |
| FI-12 | Narrative pressures for immediate settlement | LLM |

Each detected indicator returns `{code, label, evidence, weight}`. Risk banding is in `src/config.py`.

### 2.7 Graph state — `src/state.py`

**Frozen after P3.** Phases 4–6 reconcile against these names.

```python
class TriageState(TypedDict, total=False):
    run_id: str
    thread_id: str
    claim: ClaimRecord | None
    untrusted: QuarantinedText | None      # SPEC-03
    intent: Intent | None
    classification: Classification | None
    coverage: CoverageAssessment | None
    fraud: FraudAssessment | None
    routing: RoutingDecision | None
    decision: TriageDecision | None
    retrieved: list[RetrievedClause]
    memory_recall: list[str]
    messages: list[AnyMessage]
    next_step: str | None
    step_count: int
    errors: list[ErrorRecord]
    degraded: bool
    guard_input: GuardVerdict | None       # P5 fills
    guard_output: GuardVerdict | None      # P5 fills
```

All nested values are **Pydantic models** — structured output at node boundaries (§7.1 requirement).

### 2.8 Scenario matrix — what `generate_data.py` must emit

At least **12 claims** covering, at minimum:

| Scenario | Purpose |
|---|---|
| `clean_fast_track` ×2 | AC-01, AC-03 happy path |
| `clean_standard` ×2 | baseline routing |
| `high_value_escalation` | AC-03 — must escalate, never auto-approve |
| `fraud_high_investigate` | AC-02, AC-03 |
| `fraud_medium_standard` | banding boundary |
| `not_covered_exclusion` | AC-01 — exclusion clause citation |
| `lapsed_policy` | coverage `not_covered`, degradation-free failure |
| `ambiguous_out_of_scope` | AC-04 — clarify/escalate, not mishandle |
| `prompt_injection` | AC-06 — injected instructions in `description` must be refused |
| `cross_claimant_access` | AC-06 — request for another claimant's data must be refused |

## 3. Behaviour

`scripts/generate_data.py`:
- is **deterministic** — seeded (`--seed`, default `20260918`); regenerating produces byte-identical files,
  so a clean clone reproduces the eval (Rule R5);
- writes claims, policy master, policy markdown, and claim history;
- writes nothing outside `data/`;
- uses only `.invalid` email domains and `+91-90000-*` phone numbers so no value can resemble a real person;
- `--check` mode re-generates into a temp dir and diffs against `data/`, exiting non-zero on drift.

## 4. Evidence produced

| Artifact | Producer |
|---|---|
| `data/sample_claims/*.json` | `scripts/generate_data.py` |
| `data/policy_corpus/<PRODUCT>.md` ×3 | `scripts/generate_data.py` |
| `data/policy_corpus/policies.json` | `scripts/generate_data.py` |
| `data/policy_corpus/claim_history.json` | `scripts/generate_data.py` |

## 5. Done when

- [ ] `python scripts/generate_data.py` writes all four artifact groups; `--check` is clean on a re-run.
- [ ] Every claim validates against the `ClaimRecord` Pydantic model.
- [ ] Every `_fixture.expected_coverage_clause` resolves to a real heading in the corpus.
- [ ] All 10 scenarios in §2.8 are present.
- [ ] `mask()` round-trips all identifier formats and is idempotent.
- [ ] `git grep -nE '@(gmail|yahoo|outlook)\.' data/` returns nothing.
