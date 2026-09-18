# AGENTS.md — FNOL Claims-Triage Copilot · Project Table of Contents

**Business Case BC-AAIE-HACK-06** · Insurance · Agentic AI Engineer Pathway Capstone Hackathon
Source of truth: `AAIE_HACK_06_INS_FNOL_Claims_Triage_Copilot.docx` (do not edit; it is the rubric).

This file is the **map**. It tells you — human or agent — which document governs what, in what order to
read them, and who owns which phase. It contains no rules of its own; every rule lives in the document
it points to.

---

## 1. Read in this order

| # | Document | What it is | Read when |
|---|---|---|---|
| 1 | [`CLAUDE.md`](CLAUDE.md) | **Constitution.** Non-negotiable rules, fixed stack, repo layout, AC/NFR traceability, the 6-phase plan, **repository & submission rules (§6)**, **Git rules (§7)**, working guidelines | Before touching anything |
| 2 | [`design.md`](design.md) | **Architecture.** How the system actually works: graph topology, data flow, decision logic, component boundaries, cross-cutting concerns | Before writing code |
| 3 | [`specs/`](specs/) | **Buildable contracts.** One spec per component: schemas, interfaces, file formats, acceptance tests | While writing that component |
| 4 | [`README.md`](README.md) | **Runbook.** The single run command, trace regeneration, eval regeneration (graded artifact — §7.7) | When running or submitting |

Conflict order: **docx > CLAUDE.md > design.md > specs/ > code.** If a lower level disagrees with a
higher one, the lower level is a bug — fix it, don't work around it.

---

## 2. Spec index

| Spec | Covers | Phase | Primary ACs |
|---|---|---|---|
| [SPEC-01](specs/SPEC-01-data-contracts.md) | Claim / policy / state schemas, ID + masking format, synthetic data generation | P1 | R3, NFR-05 |
| [SPEC-02](specs/SPEC-02-graph-and-agents.md) | LangGraph topology, typed state, supervisor + 4 workers, routing rules, checkpointing, degradation | P2 | AC-01…04, NFR-04 |
| [SPEC-03](specs/SPEC-03-context-engineering.md) | write / select / compress / isolate, summarization middleware, quarantine of untrusted text | P3 | AC-06, NFR-03 |
| [SPEC-04](specs/SPEC-04-memory.md) | Tiered memory: SqliteSaver short-term + LangMem long-term, cross-session recall proof | P3 | AC-05 |
| [SPEC-05](specs/SPEC-05-mcp-server.md) | Custom MCP server (3 tools + 1 resource), stdio, langchain-mcp-adapters client, transcript | P3 | §7.1 |
| [SPEC-06](specs/SPEC-06-agentic-rag.md) | Agentic RAG over the synthetic policy corpus, clause citation contract | P3 | AC-01 |
| [SPEC-07](specs/SPEC-07-observability.md) | Arize Phoenix tracing, trace export, tool-invocation log, failure-mode analysis | P4 | AC-07, AC-08 |
| [SPEC-08](specs/SPEC-08-cost-governance.md) | Golden-signals report, cost/latency dashboard (PNG + CSV) | P5 | AC-09 |
| [SPEC-09](specs/SPEC-09-security-guardrails.md) | Input/output guardrails, PII masking, audit trail, secrets hygiene | P5 | AC-06, AC-10, NFR-01 |
| [SPEC-10](specs/SPEC-10-governance-pack.md) | Risk register, model card, compliance mapping, output-risk classification | P5 | AC-11 |
| [SPEC-11](specs/SPEC-11-evaluation-and-tests.md) | DeepEval golden-set harness + routing / loop / tool-contract tests | P6 | AC-12 |
| [SPEC-12](specs/SPEC-12-cli-and-runbook.md) | CLI surface, the two documented commands, citation verifier, submission checklist | P1 + P6 | NFR-02, R2, R5 |

---

## 3. Phase ownership — this engagement

| Phase | Scope | Owner | Status |
|---|---|---|---|
| **P1** | Foundation, synthetic data, secrets hygiene | **This session** | ✅ built · gates green |
| **P2** | LangGraph multi-agent core | **This session** | ✅ built · ⚠️ live-LLM run not yet executed |
| **P3** | Context engineering · memory · MCP · agentic RAG | **This session** | ✅ built · gates green |
| P4 | Arize Phoenix observability | **Teammate** | ⏳ not started |
| P5 | Cost governance · guardrails · audit · governance pack | **Teammate** | ⏳ not started |
| P6 | Evaluation · agent tests · runbook · submission | **Teammate** | ⏳ not started |

### What is verified, and what is not

Verified by actually running it, with no API key required:

| Check | Result |
|---|---|
| Synthetic corpus regenerates byte-identically (`data --check`) | ✅ 18 files, deterministic |
| Graph compiles with the frozen topology | ✅ 11 nodes, 22 edges |
| MCP server over real stdio: 3 tools + 1 resource | ✅ transcript written, responses masked |
| MCP unknown policy/claimant | ✅ `{"found": false}`, no disclosure |
| RAG retrieval hits the expected fixture clause | ✅ top-1 on all 5 probes, incl. the exclusion sweep |
| Clause-kind parsing (exclusion precedence depends on it) | ✅ 12 coverage / 11 exclusion / 9 limit / 8 procedure / 7 definition |
| Deterministic fraud scoring vs fixtures | ✅ 4/4 (high, medium, low, low) |
| Routing + escalation rules (AC-03) | ✅ all 5 cases, incl. `degraded → escalate` |
| Quarantine: `str()` raises, threats detected | ✅ 3 threat families on the injection fixture |
| Cross-session memory recall (AC-05) | ✅ `pytest` 3 passed, `logs/memory_test.log` written |
| Secret scan · Rule R4 imports · unmasked ids in `logs/` | ✅ all clean |
| CLI exit codes | ✅ `1` on missing key and missing file |

**Not yet verified — needs `GOOGLE_API_KEY` in `.env`:**

Every path that calls Gemini is built but has never executed against the live model. Specifically
unproven: intent classification (AC-04), claim type + severity extraction (AC-01), the coverage
agent's clause selection and its citation-integrity retry (AC-01, SPEC-06 §2.4), the four narrative
fraud indicators FI-09…FI-12 (AC-02), summarization middleware, and end-to-end behaviour on the
`prompt_injection` and `cross_claimant_access` fixtures (AC-06).

First thing to run once the key is in place:

```bash
.venv/bin/python -m src.main batch --dir data/sample_claims/
```

It prints a per-claim table comparing the decision against each fixture's `_fixture` oracle, so a
mismatch in routing or escalation is visible immediately. Expect prompt tuning to be needed — the
deterministic halves are correct, but the model-driven halves have never been observed.

**Handover contract (P3 → P4).** Phases 4–6 instrument and govern what Phases 1–3 build. To make that
possible without rework, Phases 1–3 ship these seams **already in place** — implemented as no-ops or thin
shims where the owning phase is later:

| Seam | Where | Left for | Why it exists now |
|---|---|---|---|
| `src/observability/tracing.py` — `init_tracing()` | called once in `src/main.py` | P4 | P4 fills in the Phoenix tracer; the call site already exists in the run path |
| `@logged_tool` decorator | `src/tools/registry.py`, on **every** tool | P4 | P4 swaps the sink to `logs/tool_calls.jsonl`; no tool has to be re-wrapped |
| `emit_audit(...)` | called at every consequential decision in `src/graph.py` | P5 | P5 fills in the JSONL writer; every call site is already correct |
| `input_guard` / `output_guard` nodes | first and last nodes of the graph | P5 | P5 fills in validators; the graph topology never changes |
| `run_id` / `thread_id` on state | `src/state.py` | P4/P6 | Failure analysis and eval both cite these |
| `mask()` on all IDs | `src/security/masking.py` | P5 | Presidio layers on top; the log-write path is already masked |

**Do not change the graph topology, state schema, or tool names after P3 closes** — P4's trace export,
P5's audit trail and P6's routing tests all reconcile against them.

---

## 4. Artifact → owner map

Where the graded evidence comes from. Full checklist in `CLAUDE.md` §4.

| Artifact | Produced by | Phase |
|---|---|---|
| `data/sample_claims/`, `data/policy_corpus/` | `scripts/generate_data.py` | P1 |
| `src/graph.py` | hand-written | P2 |
| `logs/mcp_transcript.jsonl` | `src/mcp_client.py` | P3 |
| `logs/memory_test.log` | `tests/test_memory_persistence.py` | P3 |
| `traces/phoenix_spans.parquet` | `scripts/export_traces.py` | P4 |
| `logs/tool_calls.jsonl` | `src/observability/tool_logger.py` | P4 |
| `docs/failure-analysis.md` | hand-written from **real** Phoenix runs | P4 |
| `reports/golden_signals.json` | `scripts/golden_signals.py` | P5 (+ re-run after P6) |
| `reports/dashboard.png` + `dashboard_data.csv` | Phoenix UI + `scripts/build_dashboard.py` | P5 |
| `logs/agent_actions.jsonl` | `src/observability/audit.py` | P5 |
| `docs/risk-register.md`, `model-card.md`, `compliance.md`, `output-risk.md` | hand-written, citation-gated | P5 |
| `reports/eval_report.json` | `scripts/run_eval.py` | P6 |
| `tests/test_routing.py`, `test_loops.py`, `test_tool_contracts.py` | hand-written | P6 |
| `README.md` | hand-written | P6 |

---

## 5. The five rules, in one line each

Full text in `CLAUDE.md` §1. Memorise these; they decide the grade.

1. **Evidence-in-Repo** — only committed artifacts score; evidence without producing code is heavily discounted.
2. **Citation-Resolves** — every citation in a doc must resolve to a committed artifact, or it counts as missing.
3. **Synthetic-Data** — synthetic claims and policies only; identifiers masked, never logged in plaintext.
4. **Open-Source & Gemini-Only** — Gemini is the only model provider (not Claude); pip + Python; no Docker, no external DB.
5. **Reproducibility** — one documented command runs it, a second regenerates traces and eval, from committed inputs.

---

## 6. Submission — the one-line version

The docx says **Virtusa GitLab**, not GitHub: *"Push the final repository to your assigned Virtusa
GitLab project by the cut-off."* Grading is an **automated review of the committed repository** —
7 categories / 100 marks, **no live demo**, pass at 60. A reviewer decides the grade from `git clone`
alone, so anything uncommitted is worth zero.

Git workflow is scored too: **no direct commits to `main`**, and **at least 3 PR-driven merges**
made with `git merge --no-ff` — a fast-forward merge leaves no merge commit for the static review to
find. Branch, then merge with `--no-ff`.

Full rules, the `.gitignore` trap (never ignore `logs/`, `traces/`, `reports/` — they *are* the
deliverable), the Git rules and the pre-submission checklist:
[`CLAUDE.md §6`](CLAUDE.md#6-repository--submission-rules) and
[`§7`](CLAUDE.md#7-git-section-81-and-section-2-of-the-brief).

---

## 7. Out of scope — do not spend time here

Containerized/cloud deployment · real claims-system or policy-admin integrations · front-end visual polish ·
generic unit-test volume · advanced OAuth and live secrets-rotation infrastructure (document the approach instead).
