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

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # then put your Gemini API key in GOOGLE_API_KEY
.venv/bin/python -m src.main index      # build the local RAG index (first run downloads the embedder)
```

### The two documented commands (NFR-02)

```bash
# 1 — run the copilot over the committed sample inputs
.venv/bin/python -m src.main batch --dir data/sample_claims/

# 2 — regenerate the Phoenix traces AND the evaluation      [Phase 4 / Phase 6 — in progress]
.venv/bin/python -m src.main trace && .venv/bin/python -m src.main eval
```

### Other commands

```bash
.venv/bin/python -m src.main run --claim data/sample_claims/claim_006.json   # one claim
.venv/bin/python -m src.main run --claim ... --json                          # machine-readable
.venv/bin/python -m src.main chat --claimant CLT-882134                      # what memory recalls
.venv/bin/python -m src.main data --check                                    # verify the corpus
.venv/bin/python -m src.main graph                                           # graph topology
.venv/bin/python -m pytest -q                                                # tests
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

| Artifact | Path | Phase |
|---|---|---|
| MCP tool-call transcript | `logs/mcp_transcript.jsonl` | 3 ✅ |
| Memory persistence log | `logs/memory_test.log` | 3 ✅ |
| Quarantine threat log | `logs/quarantine_events.jsonl` | 3 ✅ |
| Audit trail | `logs/agent_actions.jsonl` | 3 ✅ (Phase 5 extends) |
| Tool-invocation log | `logs/tool_calls.jsonl` | 4 |
| Phoenix trace export | `traces/phoenix_spans.parquet` | 4 |
| Failure-mode analysis | `docs/failure-analysis.md` | 4 |
| Golden signals + dashboard | `reports/golden_signals.json`, `reports/dashboard.{png,csv}` | 5 |
| Governance pack | `docs/risk-register.md`, `model-card.md`, `compliance.md`, `output-risk.md` | 5 |
| Evaluation report | `reports/eval_report.json` | 6 |

Per-run decisions are written to `runs/<run_id>_<claim_id>.json` (gitignored — regenerable).

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

Prerequisites: Python 3.11+ (3.12 used here), a Gemini API key. No Docker, no external database —
SQLite files and a local Chroma directory only.

---

## MCP servers

Two are configured in [`.mcp.json`](.mcp.json): the project's own `fnol-policy` server
(`mcp_server/server.py` — 3 tools + 1 resource, the graded artifact) and `playwright` for
development only. See [`docs/mcp-servers.md`](docs/mcp-servers.md).
