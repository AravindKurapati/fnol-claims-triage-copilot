# SPEC-11 — Agent Evaluation & Testing

**Phase 6 · teammate** · Satisfies: §7.6 all four rows · AC-12. Also feeds AC-09's quality fields.

---

## 1. Purpose & graded requirement

**Lean and agent-specific** — the rubric explicitly does not reward generic unit-test volume. Four artifacts:
a DeepEval report over a golden set, and three agent tests (routing-logic, loop/cascade guard, tool-contract).

## 2. Contract

### 2.1 Golden set — `data/golden_set.json`

Built from the `_fixture` blocks in `data/sample_claims/` (SPEC-01 §2.2), so the oracle is generated, not
hand-typed:

```jsonc
{"claim_file":"claim_001.json","question":"Triage this FNOL.",
 "expected":{"claim_type":"collision","severity":"moderate",
             "coverage_clause":"AUTO-COMP-2026 §4.2","coverage_status":"covered",
             "fraud_risk":"low","queue":"standard","escalation":false},
 "context_source":"data/policy_corpus/AUTO-COMP-2026.md"}
```

Must include the adversarial scenarios (`prompt_injection`, `cross_claimant_access`,
`ambiguous_out_of_scope`) with expected **refusal/clarification**, not a triage.

### 2.2 Evaluation harness — `scripts/run_eval.py` → `reports/eval_report.json`

**DeepEval**, LLM-as-judge = **Gemini** (Rule R4 — not Claude, not GPT).

| Metric | Measures |
|---|---|
| `HallucinationMetric` | coverage rationale unsupported by the retrieved clause text |
| `FaithfulnessMetric` | answer grounded in `state.retrieved` |
| `AnswerRelevancyMetric` | response addresses the FNOL asked |
| deterministic `routing_accuracy` | predicted queue vs `_fixture.expected_queue` |
| deterministic `escalation_recall` | **must be 1.0** — missing an escalation is the worst failure |
| deterministic `citation_validity` | cited clause exists in the corpus |

```jsonc
{"generated_at":"...","model":"<gemini id>","judge":"<gemini id>","n_cases":12,
 "metrics":{"hallucination_rate":0.08,"faithfulness":0.91,"answer_relevancy":0.93,
            "routing_accuracy":0.83,"escalation_recall":1.0,"citation_validity":1.0},
 "accuracy":0.83,
 "cases":[{"claim_id":"CLM-2026-000117","passed":true,"metrics":{...},"reason":"..."}]}
```

`accuracy` and `hallucination_rate` are read by `scripts/golden_signals.py` (SPEC-08) — **run the eval
first, then re-run golden signals.**

### 2.3 `tests/test_routing.py` — routing logic (AC-12)
Asserts the **conditional edges route the right worker for given states**, against `decide_next()` and the
pure routing function — no LLM, fully deterministic:
- unfilled `classification` → `classifier`; unfilled `coverage` → `coverage`; unfilled `fraud` → `fraud`;
  all filled → `router`
- `intent=ambiguous` → `clarify`; `intent=other_claimant_data` → `escalate` (AC-04, AC-06)
- `fraud.risk=high` → `investigate` **and** `escalation_required=True, auto_approved=False` (AC-03)
- `amount >= HIGH_VALUE_THRESHOLD` → escalated regardless of fraud risk (AC-03)
- clean low-value covered claim → `fast_track`, `auto_approved=True`
- `degraded=True` → escalated

### 2.4 `tests/test_loops.py` — loop / cascade guard (AC-12)
Asserts a **max-steps / recursion limit stops runaway loops**: force a state the supervisor cannot advance
(e.g. a worker stubbed to never fill its slot), run the graph, assert it terminates at `MAX_STEPS` with a
degraded decision rather than spinning. Assert against the explicit `step_count` check, not only
LangGraph's internal `recursion_limit`.

### 2.5 `tests/test_tool_contracts.py` — tool contracts (AC-12)
For **each** tool in `registry.list_tools()`: valid input → output validates against the declared schema;
**one error path** → typed error, not an exception (unknown policy → `{"found": false}`; malformed args →
validation error; MCP down → degradation). Enumerating the registry means a new tool cannot be added
without a contract test.

## 3. Done when

- [ ] `pytest -q` green across all four test files (including `test_memory_persistence.py` from P3).
- [ ] `reports/eval_report.json` committed, produced by `scripts/run_eval.py`, judge = Gemini.
- [ ] `escalation_recall == 1.0` and `citation_validity == 1.0`.
- [ ] `reports/golden_signals.json` re-run and its `quality` block populated from the eval.
- [ ] Adversarial golden cases expect refusal/clarification and pass.
