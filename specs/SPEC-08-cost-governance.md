# SPEC-08 — Performance & Cost Governance

**Phase 5 · teammate** · Satisfies: §7.3 both rows · AC-09.

---

## 1. Purpose & graded requirement

A **Phoenix-derived** golden-signals report and a cost/latency dashboard. Both must be derived from the
committed trace data by committed scripts — a screenshot alone, or a JSON a human could have typed, is
heavily discounted (Rule R1).

## 2. Contract

### 2.1 Golden signals — `scripts/golden_signals.py` → `reports/golden_signals.json`

Reads `px.Client().get_spans_dataframe()` (or the committed parquet), classifies each span as
**thinking** (LLM reasoning) / **acting** (agent node) / **tool** (tool call), and emits:

```jsonc
{
  "generated_at": "...", "run_ids": ["run-9f2c1ab77d04"], "source": "traces/phoenix_spans.parquet",
  "latency_ms": {
    "end_to_end": {"p50":..., "p95":..., "max":...},
    "by_span_type": { "thinking": {"p50":...,"p95":...},
                      "acting":   {"p50":...,"p95":...},
                      "tool":     {"p50":...,"p95":...} },
    "by_node":  { "classifier": {...}, "coverage": {...}, "fraud": {...}, "router": {...} },
    "by_tool":  { "retrieve_policy_clauses": {...}, "lookup_policy": {...} }
  },
  "tokens": {"input": 0, "output": 0, "total": 0, "by_node": {...}},
  "cost": {"currency":"USD", "model":"<gemini model>",
           "price_per_1k_input":..., "price_per_1k_output":...,
           "estimated_total":..., "per_claim_avg":...},
  "quality": {"accuracy":..., "hallucination_rate":..., "source":"reports/eval_report.json"},
  "throughput": {"claims_processed":..., "tool_calls":...}
}
```

**Ordering (important):** `quality` is imported from `reports/eval_report.json` (SPEC-11). Run the eval
**first**, or re-run this script after it — otherwise those fields are null and AC-09 is only half met.
Prices come from `config.PRICE_PER_1K_INPUT` / `PRICE_PER_1K_OUTPUT` (env), so the cost basis is auditable.

### 2.2 Dashboard — `reports/dashboard.png` + `reports/dashboard_data.csv`

**Both are required.** The PNG is a screenshot of the Phoenix UI latency/cost/token dashboard at
`localhost:6006` from the same run family; the CSV is the underlying data:

```python
px.Client().get_spans_dataframe().to_csv("reports/dashboard_data.csv")
```

`scripts/build_dashboard.py` writes the CSV and prints the exact Phoenix UI view to screenshot, so the pair
is reproducible.

## 3. Done when

- [ ] `reports/golden_signals.json` committed with latency by **thinking/acting/tool**, token totals, cost
      estimate, **and** populated accuracy + hallucination rate.
- [ ] `reports/dashboard.png` **and** `reports/dashboard_data.csv` both committed, from the same run.
- [ ] Both producing scripts committed; re-running reproduces the JSON/CSV.
- [ ] Numbers are internally consistent (report totals ≈ CSV totals).

## 4. Bonus (only after P6 is green)
An optimisation note with a **measured** before/after latency or cost improvement — two Phoenix-derived
golden-signals reports, plus the change that caused the delta. The summarization compression ratio recorded
by SPEC-03 §2.3 is a natural candidate.
