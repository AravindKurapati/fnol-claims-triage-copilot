# SPEC-02 — LangGraph Graph & Agents

**Phase 2** · Satisfies: §7.1 "LangGraph graph" row · AC-01, AC-02, AC-03, AC-04 · NFR-04.

---

## 1. Purpose & graded requirement

`src/graph.py` must contain: **typed state · a supervisor routing FNOL to ≥ 3 specialized worker agents ·
conditional edges · a checkpointer · structured output at node boundaries.** This is the foundation every
later phase instruments, so its topology and names are **frozen after P3**.

## 2. Contract

### 2.1 Node names — frozen

`input_guard` · `ingest` · `supervisor` · `classifier` · `coverage` · `fraud` · `router` · `clarify` ·
`escalate` · `finalize` · `output_guard`

Phase 4's trace export groups spans by these names; Phase 5's audit trail records them as `actor`;
Phase 6's routing test asserts on them. **Renaming a node is a cross-phase breaking change.**

### 2.2 Topology

```
START → input_guard → ingest → supervisor
supervisor ─(conditional)→ classifier | coverage | fraud | router | clarify | escalate
classifier → supervisor
coverage   → supervisor
fraud      → supervisor
router     ─(conditional)→ escalate | finalize
clarify    → output_guard
escalate   → output_guard
finalize   → output_guard
output_guard → END
```

Compiled with `SqliteSaver` (`langgraph-checkpoint-sqlite`) and `recursion_limit = config.MAX_STEPS`
(default 25).

### 2.3 Supervisor routing function — deterministic

```python
def decide_next(state) -> str:
    if state.degraded_beyond_recovery:        return "escalate"
    if state.intent is None:                  # first pass only — the one LLM call
        state.intent = classify_intent(...)
    if state.intent.kind == "out_of_scope":   return "clarify"
    if state.intent.kind == "ambiguous":      return "clarify"
    if state.intent.kind == "other_claimant_data": return "escalate"   # AC-06
    if state.classification is None:          return "classifier"
    if state.coverage       is None:          return "coverage"
    if state.fraud          is None:          return "fraud"
    if state.routing        is None:          return "router"
    return "escalate" if state.routing.escalation_required else "finalize"
```

Rationale in `design.md` §D1: the *intent* decision is LLM; *sequencing* is code, which keeps the supervisor
cheap in the Phase 5 cost report and makes the routing test deterministic.

**Intent kinds** (AC-04): `fnol_triage` · `claim_status` · `ambiguous` · `out_of_scope` ·
`other_claimant_data`. Only `fnol_triage` proceeds to the workers; `claim_status` is answered from memory +
history; the rest clarify or escalate — **never mishandled**.

### 2.4 Worker outputs — Pydantic, structured at the node boundary

```python
class Classification(BaseModel):            # AC-01
    claim_type: ClaimType
    severity: Literal["minor","moderate","major","catastrophic"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]                     # spans from the claim that justify it

class CoverageAssessment(BaseModel):        # AC-01 — clause citation is mandatory
    status: Literal["covered","partially_covered","not_covered","ambiguous"]
    cited_clause: str | None                # e.g. "AUTO-COMP-2026 §4.2"; None only if status=="ambiguous"
    clause_text: str | None
    applicable_limit: float | None
    deductible: float | None
    rationale: str

class FraudAssessment(BaseModel):           # AC-02 — indicators, not just a score
    risk: Literal["low","medium","high"]
    score: float
    indicators: list[FraudIndicator]        # {code, label, evidence, weight}

class RoutingDecision(BaseModel):           # AC-03
    queue: Literal["fast_track","standard","investigate"]
    rationale: str
    escalation_required: bool
    escalation_reason: str | None
    auto_approved: bool                     # MUST be False whenever escalation_required

class TriageDecision(BaseModel):            # what the CLI prints and the eval scores
    claim_id: str
    classification: Classification | None
    coverage: CoverageAssessment | None
    fraud: FraudAssessment | None
    routing: RoutingDecision | None
    next_action: str
    degraded: bool
    run_id: str
```

**Validator (AC-03, hard):** `RoutingDecision` rejects `escalation_required and auto_approved`.
**Validator (AC-01):** `CoverageAssessment` rejects a non-`ambiguous` status with `cited_clause is None`.

### 2.5 Routing rules — `src/agents/router.py`, pure function

```python
FAST_TRACK_MAX        = config.FAST_TRACK_MAX_AMOUNT        # default 50_000 INR
HIGH_VALUE_THRESHOLD  = config.HIGH_VALUE_THRESHOLD         # default 500_000 INR
INVESTIGATE_MIN_INDICATORS = config.INVESTIGATE_MIN_INDICATORS  # default 3
```

| Queue | Condition |
|---|---|
| `investigate` | `fraud.risk == "high"` or `len(fraud.indicators) >= INVESTIGATE_MIN_INDICATORS` |
| `fast_track` | `fraud.risk == "low"` and `coverage.status == "covered"` and `amount < FAST_TRACK_MAX` and `severity in {minor, moderate}` |
| `standard` | otherwise |

```
escalation_required = fraud.risk == "high"
                   or amount >= HIGH_VALUE_THRESHOLD
                   or coverage.status == "ambiguous"
                   or state.degraded
auto_approved = (not escalation_required) and queue == "fast_track"
```

No LLM participates in this function. It is asserted in `tests/test_routing.py` (P6).

## 3. Behaviour

### 3.1 Async & resilience — NFR-04
Every node is `async def`. Every model/tool call goes through `src/resilience.py::with_resilience(...)`:
per-call timeout (`TOOL_TIMEOUT_S`, default 20), bounded retry (`MAX_RETRIES`, default 2) with exponential
backoff, and on final failure an `ErrorRecord` appended to `state.errors` plus `state.degraded = True`.

**Graceful degradation matrix:**

| Failure | Behaviour |
|---|---|
| RAG unavailable | coverage falls back to `policies.json` coverage list → `status="ambiguous"`, escalate |
| MCP unavailable | fraud runs with deterministic indicators only, marks `degraded` |
| Gemini call fails after retries | that worker writes a `None`-confidence result; supervisor routes to `escalate` |
| Step limit hit | graph raises `GraphRecursionError`; CLI catches, writes a degraded decision, exits 2 |

A degraded run **still produces a decision and an audit record** — a crashed run produces neither, which is
worth nothing under Rule R1.

### 3.2 Loop guard
`step_count` increments on every node. `MAX_STEPS` is enforced both by LangGraph's `recursion_limit` and by
an explicit check in `supervisor` (belt and braces — `tests/test_loops.py` asserts the explicit one so the
test does not depend on framework internals).

### 3.3 Seams for later phases
`input_guard` / `output_guard` are **pass-through in P2** and return an `allow` verdict; P5 fills the
validators without touching topology. `emit_audit()` is called at: `input_screened`, `intent_identified`,
`coverage_decided`, `fraud_assessed`, `routing_decided`, `escalated`/`finalized`/`clarification_requested`,
`output_screened` — call sites exist in P2, body lands in P5.

## 4. Evidence produced

| Artifact | Producer |
|---|---|
| `src/graph.py`, `src/state.py`, `src/agents/*` | hand-written (the §7.1 graph row) |
| structured decision printed + written to `runs/<run_id>.json` | `src/main.py` |

## 5. Done when

- [ ] `python -m src.main run --claim data/sample_claims/claim_001.json` returns a full `TriageDecision`.
- [ ] Coverage output cites a clause id that exists in the corpus (AC-01).
- [ ] Fraud output lists indicator codes **with evidence** (AC-02).
- [ ] `high_value_escalation` and `fraud_high_investigate` fixtures both come back with
      `escalation_required=True, auto_approved=False` (AC-03).
- [ ] `ambiguous_out_of_scope` routes to `clarify`, not to a worker (AC-04).
- [ ] A checkpoint row exists in the SQLite file after a run.
- [ ] Killing the MCP server mid-run yields a degraded decision, not a traceback (NFR-04).
