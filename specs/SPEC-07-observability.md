# SPEC-07 — Arize Phoenix Observability

**Phase 4 · teammate** · Satisfies: §7.2 all four rows · AC-07, AC-08 · NFR-06.

---

## 1. Purpose & graded requirement

Phoenix is **mandated** and is the single source for every trace, latency, token and cost claim in the
submission. Four artifacts: instrumentation that is *called*, a committed trace export, a machine-generated
tool-invocation log, and an evidence-linked failure-mode analysis.

## 2. Contract

### 2.1 Instrumentation — `src/observability/tracing.py`

```python
def init_tracing(project_name: str = "fnol-triage") -> str   # returns run_id
def current_run_id() -> str
def span_ids() -> tuple[str, str]                            # (trace_id, span_id) for citation
```

- `pip install arize-phoenix openinference-instrumentation-langchain`; local in-process, UI `localhost:6006`.
- `LangChainInstrumentor().instrument()` + an OTel tracer provider pointed at the local collector.
- **The call site already exists** — `src/main.py` calls `init_tracing()` once at startup (P1 shim).
  Fill the body; do not move the call. "Imported but not called" is explicitly marked down.
- `run_id` is written onto `TriageState.run_id` so every artifact and every doc citation share one key.

### 2.2 Trace export — `scripts/export_traces.py` → `traces/phoenix_spans.parquet`

```python
px.Client().get_spans_dataframe().to_parquet("traces/phoenix_spans.parquet")
```

Must contain: **≥ 1 full run**, spans across **multiple agents** *and* **every tool call**, with latencies
present. Verify before committing: distinct `name` values include all worker nodes and all registry tool
names; `latency_ms` (or start/end) non-null on every span.

### 2.3 Tool-invocation log — `logs/tool_calls.jsonl` (AC-07)

The decorator already wraps every tool (`src/tools/registry.py`, P1 shim). Fill the sink. One object per call:

```jsonc
{"timestamp":"2026-09-18T11:02:03.118Z","agent":"coverage","tool_name":"retrieve_policy_clauses",
 "args":{"query":"collision damage","product":"AUTO-COMP-2026","k":4},
 "result":{"clause_ids":["AUTO-COMP-2026 §4.2"],"n":4},
 "latency_ms":142,"status":"ok","run_id":"run-9f2c1ab77d04","span_id":"a1b2c3d4"}
```

`status ∈ {ok, error, timeout}`. Args/results pass through `mask_record()` (NFR-05). **`tool_name` must
equal the registry name** — reconciliation is what AC-07 is actually testing.

### 2.4 Failure-mode analysis — `docs/failure-analysis.md` (AC-08)

**≥ 3 real failures from your own runs.** Each entry:

```markdown
### F-01 — Coverage agent cited a clause from the wrong product
- **Observed:** run_id `run-9f2c1ab77d04`, span_id `7c4e...`  (traces/phoenix_spans.parquet)
- **Symptom:** ...
- **Root cause:** retrieval was unfiltered by `product`
- **Fix:** metadata filter in `src/tools/rag_tool.py:retrieve_policy_clauses` (commit `abc1234`)
- **Verified by:** run_id `run-b31f...` — correct clause; `tests/...` added
```

Rule R2: every cited `run_id`/`span_id` **must resolve in the committed parquet**. Then **apply the fixes
and re-export** so the committed trace reflects the fixed system.

Candidate real failures to look for (do not invent — find them): wrong-product clause citation, MCP timeout
under retry, supervisor loop on an unfillable slot, structured-output schema violation, quarantine bypass
via a fence-breakout payload, severity drift on high-value claims.

## 3. Evidence produced

`src/observability/tracing.py` · `src/observability/tool_logger.py` · `traces/phoenix_spans.parquet` ·
`logs/tool_calls.jsonl` · `docs/failure-analysis.md` · `scripts/export_traces.py`

## 4. Done when

- [ ] Phoenix UI shows a full multi-agent run at `localhost:6006`.
- [ ] Parquet committed; contains all node names + all tool names; no null latency.
- [ ] `logs/tool_calls.jsonl` non-trivial; `set(tool_name) ⊆ registry.list_tools()` and covers every tool used.
- [ ] `docs/failure-analysis.md` has ≥ 3 entries, each citing ids that **resolve** in the committed parquet.
- [ ] Fixes applied and re-exported; no masked-identifier leak in the log.
