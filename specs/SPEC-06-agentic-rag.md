# SPEC-06 — Agentic RAG over the Policy Corpus

**Phase 3** · Satisfies: §7.1 "Agentic-RAG tool" row — `src/tools/rag_tool.py` + `data/policy_corpus/`,
**retrieval-in-the-loop** over a synthetic policy-and-coverage corpus. Supplies the clause citation for AC-01.

---

## 1. Purpose & graded requirement

Not a pre-fetch pipeline. The coverage agent **decides** to retrieve, inspects what it got, and **may
re-query** with a refined query before answering. The returned `clause_id` is what the agent must cite —
and what Phase 6's faithfulness eval scores against.

## 2. Contract

### 2.1 Index

| Setting | Value |
|---|---|
| Store | **Chroma**, persisted to `config.CHROMA_DIR` (default `var/chroma/`), gitignored |
| Embeddings | **Sentence-Transformers** `all-MiniLM-L6-v2`, local, no hosted embedding API (Rule R4) |
| Chunking | **clause-level** — one chunk per `§x.y` heading (SPEC-01 §2.4 regex), never fixed-size windows |
| Metadata | `{product, clause_id, clause_title, section, path, kind}` where `kind ∈ {coverage, exclusion, limit, procedure, definition}` |
| Build | `build_index(force=False)` — idempotent; rebuilds when the corpus checksum changes |

Clause-level chunking is the design choice that makes citation possible: a chunk **is** a clause, so the
`clause_id` in the answer is guaranteed to exist rather than being reconstructed by the model.

### 2.2 Tool — `src/tools/rag_tool.py`

```python
@logged_tool(name="retrieve_policy_clauses")
async def retrieve_policy_clauses(
    query: str,
    product: str | None = None,          # metadata filter — scope to the claim's policy product
    kind: str | None = None,             # "coverage" | "exclusion" | "limit" | "procedure" | "definition"
    k: int = 4,
) -> list[RetrievedClause]: ...

class RetrievedClause(BaseModel):
    clause_id: str          # "AUTO-COMP-2026 §4.2"
    clause_title: str
    text: str
    kind: str
    score: float
```

### 2.3 The agentic loop — in `src/agents/coverage.py`

```
1. Gemini is given the tool and the claim facts; it formulates the first query.
2. Retrieve (filtered to the claim's product).
3. Self-check: does any returned clause address THIS loss type, and did we check exclusions?
   - if no coverage clause matched      → re-query with loss-type synonyms      (≤ MAX_RAG_HOPS)
   - if a coverage clause matched       → a second, mandatory query for kind="exclusion"
   - if still nothing after the budget  → status="ambiguous" → escalate
4. Answer citing clause_id(s) actually present in `retrieved`.
```

`MAX_RAG_HOPS = config.MAX_RAG_HOPS` (default 3) — bounded, so the loop guard (P6) has a finite target.
The mandatory exclusion hop is why `not_covered_exclusion` fixtures resolve correctly: a coverage match
alone is not an answer.

### 2.4 Citation integrity — enforced, not hoped for

Post-condition in `coverage.py`: `CoverageAssessment.cited_clause` **must** be a `clause_id` present in
`state.retrieved`. If the model cites anything else, the result is rejected and the node retries once with
the violation fed back; a second violation → `status="ambiguous"` → escalate. This is the structural
defence against the hallucination metric in Phase 6.

## 3. Behaviour

- Empty index → `build_index()` runs on first use; if it fails, the tool returns `[]` and the run degrades
  to `ambiguous` + escalate (NFR-04), never a crash.
- Queries are built from **trusted** claim fields plus the classification; quarantined narrative may inform
  the query only after passing through `render_for_prompt` in the query-formulation prompt — the query
  string itself is model-authored and then length-capped.
- Retrieval is scoped by `product` so one claimant's policy product cannot surface another's clauses.

## 4. Evidence produced

| Artifact | Producer |
|---|---|
| `src/tools/rag_tool.py` | hand-written |
| `data/policy_corpus/` | `scripts/generate_data.py` (SPEC-01) |
| retrieval calls in `logs/tool_calls.jsonl` | `@logged_tool` (P4 sink) |
| multi-hop evidence in `traces/phoenix_spans.parquet` | P4 export — repeat retrieval spans in one run |

## 5. Done when

- [ ] `build_index()` indexes every clause in all 3 products; count matches the heading count.
- [ ] A claim whose loss type needs a synonym hop shows **≥ 2** retrieval calls in one run.
- [ ] Every non-ambiguous coverage answer cites a clause id present in `state.retrieved`.
- [ ] `not_covered_exclusion` fixture cites the **exclusion** clause, not the coverage clause.
- [ ] A forced citation violation triggers the retry, then degrades to `ambiguous`.
- [ ] Deleting the Chroma dir and re-running rebuilds the index with no manual step (Rule R5).
