# design.md — FNOL Claims-Triage Copilot

Architecture and design rationale. **What** to build is in `CLAUDE.md`; **how each part is contracted** is in
`specs/`; **how the whole thing works** is here.

---

## 1. Design goal

Turn an FNOL (First Notice of Loss) submission into a **routing decision with an audit trail**:

```
synthetic FNOL  →  classify (type + severity)
                →  check coverage against the policy, citing the clause
                →  screen for fraud indicators
                →  route: fast-track | standard | investigate
                →  escalate to a human when fraud is suspected or value is high
```

The graded difficulty is not the triage logic — it is that **every step must be observable, cost-governed,
guardrailed, audited, compliant and continuously evaluated**, with all of it provable from committed files.
So the architecture is shaped by one constraint above all others:

> **Every decision the system makes must leave a machine-generated trace with a stable identifier that a
> document can cite.**

That is why `run_id` and `thread_id` live on the graph state from Phase 1, why every tool goes through one
registry, and why every consequential decision goes through one `emit_audit()` call.

---

## 2. System context

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  CLI  (src/main.py)   —  the required interface;  FastAPI is bonus only       │
│    run · trace · eval · demo                                                  │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │ claim json (committed sample input)
                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LangGraph app  (src/graph.py)                                                │
│                                                                               │
│   input_guard → ingest → ┌──────────────┐ ──▶ classifier ─┐                   │
│                          │  SUPERVISOR  │ ──▶ coverage   ─┤                   │
│                          │  (hub/router)│ ──▶ fraud      ─┼─▶ back to hub     │
│                          └──────┬───────┘ ──▶ router     ─┘                   │
│                                 │                                             │
│                    ┌────────────┼────────────┬─────────────┐                  │
│                    ▼            ▼            ▼             ▼                  │
│                 clarify     escalate      finalize    (degraded exit)         │
│                                 │            │                                │
│                                 └────┬───────┘                                │
│                                      ▼                                        │
│                                 output_guard → END                            │
│                                                                               │
│   state: TriageState (typed)   ·   checkpointer: SqliteSaver                   │
└───┬──────────────┬───────────────┬────────────────┬──────────────────────────┘
    │              │               │                │
    ▼              ▼               ▼                ▼
┌─────────┐  ┌───────────┐  ┌────────────┐  ┌──────────────────┐
│ Gemini  │  │ MCP server│  │ Agentic RAG│  │ Tiered memory    │
│ (only   │  │ stdio     │  │ Chroma +   │  │ SqliteSaver      │
│ provider│  │ 3 tools   │  │ S-Transf.  │  │  + LangMem store │
└─────────┘  │ 1 resource│  │ policy     │  └──────────────────┘
             └───────────┘  │ corpus     │
                            └────────────┘
    ▲              ▲               ▲                ▲
    └──────────────┴───────┬───────┴────────────────┘
                           │  every call passes through
                  ┌────────┴─────────────────────────────┐
                  │ cross-cutting spine                  │
                  │  · tool registry  (@logged_tool)     │
                  │  · tracing        (Phoenix / OTel)   │
                  │  · audit          (agent_actions)    │
                  │  · masking        (PII / ids)        │
                  │  · quarantine     (untrusted text)   │
                  └──────────────────────────────────────┘
```

---

## 3. Key design decisions

### D1 — Supervisor as a **hub**, not a one-shot planner
Workers return to the supervisor, which re-inspects state and picks the next worker. Chosen over a fixed
linear chain because:
- it is the pattern the rubric names ("a supervisor routing new claims to specialized worker agents");
- it produces **genuine conditional edges** rather than decorative ones;
- it creates a real loop, which makes the **recursion/step limit** (`tests/test_loops.py`) a meaningful
  guard instead of a formality;
- it lets a degraded worker be skipped without collapsing the run.

Cost: more LLM turns. Mitigated by making the supervisor's next-step choice **deterministic** (it reads
which state slots are unfilled) and reserving the LLM for the **intent** decision only. This also keeps the
supervisor cheap in the Phase 5 cost report.

### D2 — Deterministic decisions where the rubric says "never"
AC-03 says suspected-fraud or high-value claims are **escalated to a human, not auto-approved**. A prompt
instruction is not a control. The escalation rule is a pure function in `src/agents/router.py`:

```python
escalation_required = (fraud.risk == HIGH) or (claim.estimated_amount >= HIGH_VALUE_THRESHOLD) \
                      or (coverage.status == AMBIGUOUS)
```

It is asserted in `tests/test_routing.py`, recorded in `logs/agent_actions.jsonl`, and re-checked by the
output guard. Three independent layers, because this is the single claim most likely to be probed.

**Division of labour:** the LLM *extracts and reasons* (what type of claim, which clause applies, which
fraud indicators are present in the narrative); **code decides** (risk banding, routing, escalation). Every
consequential threshold lives in `src/config.py`, not in a prompt.

### D3 — Untrusted text is a **different type**
Claimant free text (`claim.description`, any follow-up message) is never a plain string in state. It is
`QuarantinedText`, and the only way to put it in a prompt is `render_for_prompt()`, which wraps it in a
data fence with an explicit non-instruction preamble. This makes NFR-03 structurally enforced rather than
remembered: a developer who forgets gets a type they cannot concatenate.

### D4 — One tool registry, one decorator
Native tools, MCP-adapted tools and the RAG tool all register through `src/tools/registry.py` and all wear
`@logged_tool`. Consequences the rubric cares about:
- **AC-07** "tool names reconcile with the agent/MCP code" is true by construction — the log name *is* the
  registry name;
- Phase 4 changes one sink, not twenty call sites;
- the tool-contract test (AC-12) enumerates the registry rather than a hand-kept list.

### D5 — Masking at the boundary, not at the call site
`src/security/masking.py` exposes `mask_record()`, applied inside the tool logger, the audit writer and the
CLI printer. Nothing downstream needs to remember. Policy numbers and claimant ids are masked to their last
3 characters; Presidio (Phase 5) layers free-text PII detection on top of the same chokepoints.

### D6 — Two memory tiers with different jobs
- **Short-term** = LangGraph `SqliteSaver` checkpointer, keyed by `thread_id`. Gives resumability and
  in-conversation context (AC-05 first half) for free.
- **Long-term** = LangMem semantic store, namespaced `("claimant", claimant_id)`. Survives process exit.
  This is what makes "recalls prior-session context on a return visit" (AC-05 second half) provable: the
  test runs session A, **tears down the app object**, builds a fresh one, and asserts recall in session B.

### D7 — RAG is *agentic*, not a pre-fetch
`retrieve_policy_clauses` is a **tool the coverage agent chooses to call**, and may call again with a
refined query when the first result does not cover the loss type. It returns `clause_id`s, which the
coverage agent must quote in `coverage.cited_clause` — that citation is AC-01 and is checked by the
faithfulness eval in Phase 6.

### D8 — Async everywhere a model or tool is called
NFR-04. Every node is `async def`; every tool call is wrapped by `with_resilience()` — timeout, bounded
retry with backoff, and on final failure a **typed degradation** written to `state.errors` plus
`state.degraded = True`. A degraded run still produces a decision: it routes to `escalate` with rationale
"degraded: <component> unavailable". Crashing is never an acceptable outcome, because a crashed run
produces no trace and no audit record.

### D9 — Seams for Phases 4–6 are built in Phase 1–3
See `AGENTS.md` §3. `init_tracing()`, `@logged_tool`, `emit_audit()`, `input_guard`/`output_guard` nodes and
`run_id`/`span` plumbing all exist as shims from the start. The teammate owning P4–P6 fills bodies; they
never restructure the graph. This protects the artifacts that reconcile against topology and tool names.

---

## 4. Data flow of one claim

| # | Node | Reads | Writes | Side effects |
|---|---|---|---|---|
| 1 | `input_guard` | raw input | `guard_input` verdict | audit(`input_screened`); may terminate with refusal |
| 2 | `ingest` | raw claim json | `claim` (validated), `untrusted` (quarantined), `run_id`, memory recall | long-term memory read |
| 3 | `supervisor` | full state | `next_step`, `intent` | audit(`intent_identified`) on first pass |
| 4 | `classifier` | claim, untrusted | `classification{claim_type, severity, confidence, evidence}` | Gemini |
| 5 | `coverage` | claim, classification | `coverage{status, cited_clause, limit, deductible, rationale}` | MCP `lookup_policy`, RAG `retrieve_policy_clauses`, Gemini |
| 6 | `fraud` | claim, untrusted, history | `fraud{risk, indicators[], score}` | MCP `check_claim_history`, Gemini |
| 7 | `router` | classification, coverage, fraud | `routing{queue, rationale, escalation_required}` | **deterministic**; audit(`routing_decided`) |
| 8 | `escalate` \| `finalize` \| `clarify` | routing | `decision` | audit(`escalated` \| `finalized` \| `clarification_requested`) |
| 9 | `output_guard` | decision | `guard_output` verdict | audit(`output_screened`); may redact or block |

Every node writes memory via the context layer's `write` strategy and bumps `step_count`.

---

## 5. Decision model

**Claim types** (per line of business) — auto: `collision`, `theft`, `glass`, `third_party_liability`;
property: `fire`, `water_damage`, `burglary`, `storm`; liability: `bodily_injury`, `property_damage`.

**Severity** — `minor` | `moderate` | `major` | `catastrophic`, derived from estimated amount banding,
injury presence and business-interruption signals.

**Coverage status** — `covered` | `partially_covered` | `not_covered` | `ambiguous`. Every status but
`ambiguous` **must** carry a `cited_clause` resolving to a real clause id in `data/policy_corpus/`.

**Fraud risk** — `low` | `medium` | `high`, from a weighted indicator score. Indicators are a fixed catalog
(`FI-01`…`FI-12`) with deterministic detectors where possible (late reporting, policy recently incepted,
round-number amount, claim near policy limit, prior-claim frequency) and LLM-assessed narrative indicators
where not (internal inconsistency, coached language, unverifiable detail). Each returned indicator carries
its code **and** the evidence that triggered it — AC-02 requires the indicators, not just the signal.

**Routing** —

| Queue | Condition |
|---|---|
| `fast_track` | `fraud.risk == low` **and** `coverage == covered` **and** `amount < FAST_TRACK_MAX` **and** `severity in {minor, moderate}` |
| `investigate` | `fraud.risk == high` **or** ≥ `INVESTIGATE_INDICATOR_COUNT` indicators |
| `standard` | everything else |
| — escalation flag — | `fraud.risk == high` **or** `amount >= HIGH_VALUE_THRESHOLD` **or** `coverage == ambiguous` |

Escalation is orthogonal to queue: a claim can be `standard` **and** escalated. When escalated, the decision
is a **recommendation for a human**, and `auto_approved` is `false` — always.

---

## 6. Cross-cutting concerns

| Concern | Module | Enforced how |
|---|---|---|
| Untrusted input | `src/context/quarantine.py` | type-level; `QuarantinedText` cannot be string-concatenated into a prompt |
| PII / identifiers | `src/security/masking.py` | applied inside tool logger, audit writer, CLI renderer |
| Tool observability | `src/tools/registry.py` | `@logged_tool` on every registration |
| Tracing | `src/observability/tracing.py` | `init_tracing()` called once in the CLI entrypoint (P4 fills body) |
| Audit | `src/observability/audit.py` | `emit_audit()` at each consequential decision (P5 fills body) |
| Resilience | `src/resilience.py` | `with_resilience()` wraps every model and tool call |
| Config | `src/config.py` | all thresholds, model names, prices, paths — env-driven, no literals in agents |

---

## 7. What deliberately is **not** here

- **No container, no server DB.** SQLite file + local Chroma persist dir. (Rule R4 / §6.2)
- **No second model provider.** Gemini for agents *and* for the DeepEval judge; embeddings are local
  Sentence-Transformers, not a hosted embedding API.
- **No web UI.** CLI is the required interface; FastAPI streaming is bonus and only after P6 is green.
- **No real integrations.** MCP tools read committed synthetic fixtures — that is the point of the
  Synthetic-Data rule, and it keeps the run reproducible from a clean clone.
- **No hand-written evidence.** If a report, log or trace is missing, the fix is to run the system and
  export — never to author the file.
