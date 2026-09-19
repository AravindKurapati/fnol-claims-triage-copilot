# FNOL Claims-Triage Copilot

Agentic claims triage for First Notice of Loss: classify the claim, check coverage against the policy
with a clause citation, screen for fraud indicators, and route it — fast-track, standard or
investigate — with an audit trail, escalating to a human whenever fraud is suspected or the value is
high.

Business Case **BC-AAIE-HACK-06** · Agentic AI Engineer Pathway capstone.

> **Start here:** [`AGENTS.md`](AGENTS.md) is the project map. [`CLAUDE.md`](CLAUDE.md) holds the rules
> and the 6-phase plan, [`design.md`](design.md) the architecture, [`specs/`](specs/) the per-component
> contracts.

---

## Quick start

Prerequisites: **Python 3.11+** (3.12 used here) and a **Gemini API key** (the free tier works; the
agents pace themselves to it). No Docker, no external database.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt          # Windows: .venv\Scripts\pip ...

cp .env.example .env          # then put your Gemini API key in GOOGLE_API_KEY
.venv/bin/python -m src.main index      # build the local RAG index (first run downloads the embedder)
```

### The two documented commands (NFR-02)

```bash
# 1 — run the copilot over the committed sample inputs
.venv/bin/python -m src.main batch --dir data/sample_claims/

# 2 — regenerate the Phoenix traces AND the evaluation
.venv/bin/python -m src.main trace && .venv/bin/python -m src.main eval
```

**Command 2** does the following:

1. `trace` starts a local Arize Phoenix server on `http://localhost:6006` if one is not already
   running (log: `var/phoenix_server.log`).
2. It then runs all 12 sample claims with OpenInference instrumentation and exports the run's spans
   to `traces/phoenix_spans.parquet` (`scripts/export_traces.py`).
3. `eval` rebuilds `data/golden_set.json` from the claims' oracles (`scripts/build_golden_set.py`).
4. It scores that run's decisions with DeepEval, using **Gemini as the judge**, plus deterministic
   routing, escalation and citation checks (`scripts/run_eval.py` → `reports/eval_report.json`).
5. It regenerates the golden signals from the spans (`scripts/golden_signals.py` →
   `reports/golden_signals.json`).
6. It rebuilds the dashboard (`scripts/build_dashboard.py` → `reports/dashboard.png`,
   `reports/dashboard_data.csv`).

On the free tier, `trace` takes about 5–10 minutes and `eval` about 10 minutes. Use
`eval --no-judge` for the deterministic metrics alone, with no model calls.

### Other commands

```bash
.venv/bin/python -m src.main run --claim data/sample_claims/claim_006.json   # one claim
.venv/bin/python -m src.main run --claim ... --json                          # machine-readable
.venv/bin/python -m src.main chat --claimant CLT-882134                      # what memory recalls
.venv/bin/python -m src.main data --check                                    # verify the corpus
.venv/bin/python -m src.main graph                                           # graph topology
.venv/bin/python -m src.main verify                                          # citation + hygiene verifier
.venv/bin/python scripts/redteam.py                                          # guardrail red-team
.venv/bin/python -m pytest -q                                                # agent tests
```

Exit codes: `0` ok · `1` usage/config error · `2` degraded run (decision produced, escalated) ·
`3` blocked by a guardrail.

---

## Committed sample inputs

12 synthetic claims in [`data/sample_claims/`](data/sample_claims/), generated deterministically by
[`scripts/generate_data.py`](scripts/generate_data.py) and covering ten scenarios:

| File | Scenario | What it exercises |
|---|---|---|
| `claim_001`, `claim_002` | `clean_fast_track` | happy-path routing, auto-approval |
| `claim_003`, `claim_004` | `clean_standard` | baseline triage |
| `claim_005` | `high_value_escalation` | AC-03 — covered and clean, but escalated on value |
| `claim_006` | `fraud_high_investigate` | AC-02/AC-03 — 7 indicators, SIU referral |
| `claim_007` | `fraud_medium_standard` | risk-banding boundary |
| `claim_008` | `not_covered_exclusion` | AC-01 — exclusion overrides cover |
| `claim_009` | `lapsed_policy` | period-of-insurance clause |
| `claim_010` | `ambiguous_out_of_scope` | AC-04 — clarify, never mishandle |
| `claim_011` | `prompt_injection` | AC-06/NFR-03 — injected instructions have no effect |
| `claim_012` | `cross_claimant_access` | AC-06 — refused without disclosure |

Each carries a `_fixture` block — the oracle for the Phase 6 golden set and routing tests. It is
stripped at ingest and never reaches a prompt.

All data is synthetic (Rule R3): `.invalid` email domains, reserved `+91-90000-*` phone numbers, and
policy/claimant identifiers masked to their last three characters everywhere they are printed or
logged.

---

## Where the evidence lands

Every artifact below is written by committed code (the producer column). `scripts/evidence_manifest.py`
declares each pairing, and `python -m src.main verify` checks it.

| Artifact | Path | Producer |
|---|---|---|
| Phoenix trace export | `traces/phoenix_spans.parquet`, `traces/trace_manifest.json` | `scripts/export_traces.py` |
| Tool-invocation log (AC-07) | `logs/tool_calls.jsonl` | `src/observability/tool_logger.py` |
| Audit trail (AC-10) | `logs/agent_actions.jsonl` | `src/observability/audit.py` |
| MCP transcript | `logs/mcp_transcript.jsonl` | `src/mcp_client.py` |
| Memory persistence log (AC-05) | `logs/memory_test.log` | `tests/test_memory_persistence.py` |
| Quarantine threat log | `logs/quarantine_events.jsonl` | `src/context/quarantine.py` |
| Golden signals (AC-09) | `reports/golden_signals.json` | `scripts/golden_signals.py` |
| Dashboard (AC-09) | `reports/dashboard.png`, `reports/dashboard_data.csv`, `reports/dashboard_chart.png` | `scripts/build_dashboard.py` |
| Evaluation report (AC-12) | `reports/eval_report.json` | `scripts/run_eval.py` |
| Golden set | `data/golden_set.json` | `scripts/build_golden_set.py` |
| Red-team results | `reports/redteam_results.json` | `scripts/redteam.py` |
| PII before/after sample | `reports/pii_redaction_sample.json` | `scripts/redteam.py` |
| Optimisation note | `reports/optimization_note.md` | measured from `reports/golden_signals_baseline.json` vs `reports/golden_signals.json` |
| Failure analysis (AC-08) | `docs/failure-analysis.md` | written from real Phoenix runs; ids resolved by the verifier |
| Governance pack (AC-11) | `docs/risk-register.md`, `docs/model-card.md`, `docs/compliance.md`, `docs/output-risk.md` | citation-gated by the verifier |

Per-run decisions are written to `runs/<run_id>_<claim_id>.json` (gitignored, regenerable). The
evaluation scores these files, which are the same decisions whose spans are in the trace export.

---

## Architecture in one paragraph

A LangGraph graph with a **supervisor hub**: `input_guard → ingest → supervisor`, and the supervisor
routes to one of four specialized workers (`classifier`, `coverage`, `fraud`, `router`) via
conditional edges, with workers returning to the hub until every state slot is filled. Sequencing is
deterministic — the LLM decides *intent* only. Coverage runs retrieval-in-the-loop over the policy
corpus and must cite a clause that was actually retrieved. Fraud combines eight code-computed
indicators with four narrative ones the model may only *add*. Routing and escalation are a pure
function: an escalated claim can never be auto-approved, enforced in the router, in the
`RoutingDecision` validator, and again at the output guard. Full detail in [`design.md`](design.md).

Storage is SQLite files and a local Chroma directory only, all under the gitignored `var/`.

---

## MCP servers

Two are configured in [`.mcp.json`](.mcp.json): the project's own `fnol-policy` server
(`mcp_server/server.py` — 3 tools + 1 resource, the graded artifact) and `playwright` for
development only. See [`docs/mcp-servers.md`](docs/mcp-servers.md).

---

## Troubleshooting

- **Phoenix port.** `trace` expects Phoenix on `localhost:6006` (`PHOENIX_COLLECTOR_ENDPOINT`). If
  another process holds the port, stop it or change the endpoint in `.env`. Server output goes to
  `var/phoenix_server.log`. Set `PHOENIX_ENABLED=false` to run without tracing.
- **First run is slow.** Sentence-Transformers downloads `all-MiniLM-L6-v2` (~90 MB) the first time
  the index or memory is built, and Presidio loads a spaCy model.
- **429 / RESOURCE_EXHAUSTED.** Free-tier quotas are per model and per minute. Calls are paced to
  `GEMINI_RPM` (default 12) and honour the provider's retry delay. Lower `GEMINI_RPM` if 429s persist.
- **`404 NOT_FOUND` on the model.** Model ids get retired. Set `GEMINI_MODEL` / `GEMINI_JUDGE_MODEL`
  in `.env` to current ids. The startup probe makes a real call, so this fails fast.
- **Windows console.** Set `PYTHONIOENCODING=utf-8` so the rich tables render (`§`, `→`, `₹`).
