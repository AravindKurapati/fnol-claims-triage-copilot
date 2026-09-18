# SPEC-12 — CLI, Runbook & Citation Verifier

**Phase 1 (CLI skeleton) + Phase 6 (runbook, verifier)** · Satisfies: §7.7 · NFR-02 · Rules R2, R5.

---

## 1. Purpose & graded requirement

The **CLI is the required interface** (FastAPI is bonus only). NFR-02: **one** documented command runs the
copilot, **a second** regenerates the Phoenix traces and the evaluation, with committed sample inputs.

## 2. Contract

### 2.1 CLI — `src/main.py`

```
python -m src.main run    --claim data/sample_claims/claim_001.json [--thread-id T] [--json]
python -m src.main batch  --dir data/sample_claims/            # every committed sample input
python -m src.main chat   --claimant CLT-882134                # multi-turn; proves AC-05 in-session
python -m src.main data   [--check]                            # regenerate / verify synthetic data
python -m src.main index  [--force]                            # build the RAG index
python -m src.main trace                                       # P4: regenerate + export Phoenix traces
python -m src.main eval                                        # P6: run DeepEval + golden signals
python -m src.main verify                                      # P6: citation + hygiene verifier
```

Every run: calls `init_tracing()` **first** (the P4 seam), prints a human-readable decision with all
identifiers **masked**, and writes the structured decision to `runs/<run_id>.json`.

Exit codes: `0` ok · `1` usage/config error · `2` degraded run (decision produced, escalated) · `3` blocked
by guardrail.

### 2.2 The two documented commands — NFR-02

```bash
# 1 — run the copilot over the committed sample inputs
python -m src.main batch --dir data/sample_claims/

# 2 — regenerate the Phoenix traces AND the evaluation
python -m src.main trace && python -m src.main eval
```

These exact commands go in `README.md` and must work from a **clean clone** after
`pip install -r requirements.txt` and `cp .env.example .env` + a Gemini key.

### 2.3 `README.md` — the graded runbook (§7.7)

Must contain: prerequisites (Python 3.11+, a Gemini API key) · install · env setup · **the single run
command** · **how to regenerate traces (Phoenix, `localhost:6006`) and the eval** · committed sample inputs
listed · where each evidence artifact lands · architecture pointer to `design.md` · project map pointer to
`AGENTS.md` · troubleshooting (Phoenix port, first-run model download for Sentence-Transformers).

### 2.4 Citation verifier — `scripts/verify_citations.py` (Rule R2, Rule R1)

Exits non-zero on any failure. Checks:

1. every path cited in `docs/*.md` and `README.md` exists in the repo;
2. every `run_id` / `span_id` cited in `docs/failure-analysis.md` resolves in `traces/phoenix_spans.parquet`;
3. every evidence file under `logs/ traces/ reports/` has a committed producer (mapping declared in
   `scripts/evidence_manifest.py`, asserted present);
4. `tool_name` values in `logs/tool_calls.jsonl` ⊆ `registry.list_tools()` (AC-07 reconciliation);
5. no secret pattern anywhere; no unmasked policy number / claimant id in any file under `logs/`;
6. Rule R4 provider check — **scoped to what we control**:
   - no `openai` / `anthropic` / other-provider import anywhere under `src/`, `mcp_server/`, `scripts/`,
     `tests/`;
   - no other-provider package in `requirements.txt` (**direct** deps only);
   - no `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in `.env.example`;
   - no Dockerfile / compose file / k8s manifest.

   > **Known and accepted:** `deepeval` and `llm-guard` pull `openai`, `anthropic`, `langchain-openai`
   > and `langchain-anthropic` into the venv as **transitive** dependencies of approved tools. They are
   > never imported and never configured — `src/config.py::validate_provider_rules()` fails the run if
   > another provider's key is present in the environment. The verifier therefore checks direct deps and
   > source imports, not `pip freeze`. Note this in `docs/compliance.md` so a grader reading the lockfile
   > does not read it as a Rule R4 breach.

### 2.5 Bonus — `src/api/`
Optional async FastAPI **streaming** endpoint. Extra credit only, and only after Phase 6 is green.
Never trade a required artifact for it.

## 3. Done when

- [ ] All CLI subcommands work; exit codes behave as specified.
- [ ] `README.md` contains both commands and they succeed from a clean clone.
- [ ] `python -m src.main verify` exits 0 with all six checks green.
- [ ] Sample inputs are committed and referenced by name in the README.
