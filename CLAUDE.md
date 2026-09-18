# CLAUDE.md — FNOL Claims-Triage Copilot

Authoritative working agreement for this repository. It is derived **verbatim in intent** from
`AAIE_HACK_06_INS_FNOL_Claims_Triage_Copilot.docx` (Business Case **BC-AAIE-HACK-06**).
If anything in this file conflicts with that docx, **the docx wins** — fix this file, don't drift.

---

## 0. Project Identity

| Field | Value |
|---|---|
| Title | FNOL Claims-Triage Copilot |
| Business Case ID | BC-AAIE-HACK-06 |
| Domain | Insurance (First Notice of Loss) |
| Type | Agentic AI Engineer Pathway — Cross-Cutting Capstone Hackathon |
| Duration | 20 hours · Team of 2–4 |
| Evaluation | **Automated review of the committed Git repository** against a 7-category / 100-mark rubric. **No live demo judging.** |
| Submission | Push final repo to the assigned Virtusa GitLab project before cut-off |
| Pass band | ≥ 60 = Pass · < 60 = Not Yet Passed |

**Problem.** At FNOL a handler must capture the claim, classify type + severity, check coverage against
the policy, screen for fraud indicators, and route it (fast-track / standard / investigate). Manual FNOL is
slow and inconsistent; simple claims wait behind complex ones. We build an agentic copilot that triages a
new claim end-to-end and drafts the routing decision **with an audit trail**.

**What is scored:** a working LangGraph multi-agent system (context engineering, tiered memory, a custom MCP
server, an agentic-RAG tool) that is then **instrumented and governed** — Arize Phoenix observability, cost &
latency governance, guardrails & audit, governance/compliance documentation, agent-level evaluation & tests.

**What is NOT scored:** UI polish, generic unit-test volume, deployment path. Containerized/cloud
deployment is **out of scope for this cut**.

---

## 1. Non-Negotiable Rules

These are the rules the grader applies. Treat every one as a build-time constraint, not a guideline.

### R1 — Evidence-in-Repo Rule
Only **committed artifacts** are scored. A claim with no committed evidence scores **zero**. An evidence
artifact with **no producing code** (a metric, trace or log a human could have hand-written) is **heavily
discounted**.
> ⇒ Every JSON/JSONL/PNG/CSV/Parquet under `logs/`, `traces/`, `reports/` MUST be emitted by a committed
> script or middleware, and that producing code MUST be committed next to it. Never hand-author evidence.

### R2 — Citation-Resolves Rule
Wherever a doc cites evidence (a Phoenix `run_id`/`span_id`, a log record, a control file), the citation
**must resolve to a committed artifact**. Unresolvable citations are treated as missing.
> ⇒ Before committing any doc in `docs/`, verify every cited path exists and every cited span id appears in
> the committed trace export. Run `scripts/verify_citations.py` (Phase 6).

### R3 — Synthetic-Data Rule
Only **synthetic** claims and policy documents that we generate. Policy numbers and claimant identifiers
must be synthetic **and masked where shown**; **never write them to logs in plaintext**.

### R4 — Open-Source & Gemini-Only Rule
Approved open-source stack with **Google Gemini as the only model provider** — **not Claude, not OpenAI**.
Build, run and evaluate with **pip + Python**. **No Docker, no external database service.**
> ⇒ No `openai`, `anthropic`, `docker-compose`, Postgres/Redis services anywhere in the repo.
> DeepEval's LLM-as-judge must also be **Gemini**.

### R5 — Reproducibility Rule
The system, its traces and its evaluation must be regenerable **from a single documented command** with
**committed sample inputs**.

---

## 2. Fixed Technology Stack

**Do not substitute.** If a library is missing, add it — do not swap it for a favourite alternative.

| Layer | Approved tool |
|---|---|
| Language / Agent framework | **Python 3.11+** · **LangGraph** (MIT) |
| LLM provider | **Google Gemini (API) only** — not Claude |
| Interoperability | **MCP Python SDK (stdio)** + **langchain-mcp-adapters** |
| Memory | **langgraph-checkpoint-sqlite** (SQLite file) + **LangMem** |
| Retrieval | **Chroma or FAISS** + **Sentence-Transformers** (local embeddings) |
| Observability (mandated) | **Arize Phoenix** + OpenTelemetry / **openinference** (local, in-process) |
| Evaluation | **DeepEval** (LLM-as-judge = **Gemini**) · **pytest** for agent tests |
| Security | **Guardrails-AI / LLM Guard** · **Presidio** (PII) · **python-dotenv** |
| Interface | **CLI (required)** · FastAPI streaming (**optional / bonus only**) |

Phoenix runs locally: `pip install arize-phoenix openinference-instrumentation-langchain`, UI at
`http://localhost:6006`. Phoenix is **the single source** of trace, latency, token and cost evidence.

---

## 3. Repository Layout (target)

Paths are graded. Put artifacts **at these paths**.

```
.
├── CLAUDE.md                      # ← constitution: rules, stack, phases (this file)
├── AGENTS.md                      # ← table of contents for the whole project
├── design.md                      # ← architecture & design rationale
├── specs/                         # ← one buildable spec per component (SPEC-01 … SPEC-12)
├── README.md                      # local-run runbook: single-command run, trace regen, eval regen
├── .env.example                   # NFR-01 — every var, no real values
├── .gitignore                     # must cover .env, *.sqlite, __pycache__, .chroma/
├── requirements.txt
├── src/
│   ├── main.py                    # CLI entrypoint (required interface)
│   ├── config.py                  # env-driven settings: model, thresholds, prices, paths
│   ├── resilience.py              # with_resilience(): timeout, retry, typed degradation (NFR-04)
│   ├── graph.py                   # 7.1 — typed state, supervisor + ≥3 workers, conditional edges,
│   │                              #       checkpointer, structured output at node boundaries
│   ├── state.py                   # TypedDict/Pydantic graph state
│   ├── agents/
│   │   ├── supervisor.py
│   │   ├── classifier.py          # claim type + severity           (AC-01)
│   │   ├── coverage.py            # coverage check + clause cite    (AC-01)
│   │   ├── fraud.py               # fraud indicators + risk signal  (AC-02)
│   │   └── router.py              # fast-track / standard / investigate + HITL escalation (AC-03)
│   ├── context/                   # 7.1 — write / select / compress / isolate
│   │   ├── strategies.py
│   │   ├── summarization.py       # summarization middleware
│   │   └── quarantine.py          # untrusted claimant text isolation (NFR-03, AC-06)
│   ├── memory/                    # 7.1 — short-term + long/semantic (LangMem)
│   │   ├── short_term.py          # SqliteSaver checkpointer
│   │   └── long_term.py           # semantic / cross-session store
│   ├── tools/
│   │   ├── rag_tool.py            # 7.1 — agentic RAG over policy corpus
│   │   └── registry.py            # tool registration + logging decorator
│   ├── mcp_client.py              # langchain-mcp-adapters consumption
│   ├── observability/
│   │   ├── tracing.py             # 7.2 — Phoenix/openinference tracer, CALLED not just imported
│   │   ├── tool_logger.py         # emits logs/tool_calls.jsonl            (AC-07)
│   │   └── audit.py               # emits logs/agent_actions.jsonl         (AC-10)
│   ├── security/
│   │   └── masking.py             # identifier masking at every log/print chokepoint (NFR-05)
│   ├── guardrails/                # 7.4 — input + output guardrails wired into the I/O path
│   │   ├── input_guard.py
│   │   ├── output_guard.py
│   │   └── pii.py                 # Presidio free-text PII (R3, NFR-05)
│   └── api/                       # OPTIONAL bonus — FastAPI streaming
├── mcp_server/                    # 7.1 — ≥2 tools + 1 resource, stdio
│   └── server.py
├── data/
│   ├── policy_corpus/             # synthetic policy + coverage documents
│   ├── sample_claims/             # committed sample inputs (NFR-02, R5)
│   └── golden_set.json            # eval golden set
├── scripts/
│   ├── generate_data.py           # synthetic claim + policy generation (R3)
│   ├── export_traces.py           # traces/phoenix_spans.parquet
│   ├── golden_signals.py          # reports/golden_signals.json
│   ├── build_dashboard.py         # reports/dashboard_data.csv (+ PNG pairing)
│   ├── run_eval.py                # reports/eval_report.json (DeepEval, Gemini judge)
│   └── verify_citations.py        # R2 enforcement
├── runs/                          # structured decision per run, keyed by run_id
├── logs/
│   ├── quarantine_events.jsonl    # threat detections (P3)
│   ├── tool_calls.jsonl           # AC-07
│   ├── agent_actions.jsonl        # AC-10
│   ├── mcp_transcript.jsonl       # 7.1
│   └── memory_test.log            # 7.1
├── traces/
│   └── phoenix_spans.parquet      # ≥1 full run, multi-agent spans + every tool call, latencies
├── reports/
│   ├── golden_signals.json        # AC-09
│   ├── dashboard.png              # Phoenix UI screenshot
│   ├── dashboard_data.csv         # underlying data for the screenshot
│   └── eval_report.json           # AC-12
├── docs/
│   ├── failure-analysis.md        # AC-08 — ≥3 real failures w/ run_id + span_id
│   ├── risk-register.md           # AC-11
│   ├── model-card.md              # AC-11
│   ├── compliance.md              # AC-11 — EU AI Act / NIST AI RMF / DPDP
│   └── output-risk.md             # AC-11
└── tests/
    ├── test_memory_persistence.py # cross-session recall (AC-05)
    ├── test_routing.py            # AC-12
    ├── test_loops.py              # AC-12
    └── test_tool_contracts.py     # AC-12
```

---

## 4. Acceptance Criteria — traceability

Every AC must map to committed code **and** committed evidence. Keep this table true.

| ID | Criterion | Owning phase | Primary evidence |
|---|---|---|---|
| AC-01 | Classify claim type + severity; check loss vs policy, **citing the coverage clause applied** | P2/P3 | run output + `traces/` |
| AC-02 | Screen for fraud indicators; return fraud-risk signal **with triggering indicators** | P2 | run output + `traces/` |
| AC-03 | Route fast-track/standard/investigate **with rationale**; suspected-fraud or high-value → **human escalation, never auto-approved** | P2 | `logs/agent_actions.jsonl` |
| AC-04 | Identify claim intent, handle with right capability; ambiguous/out-of-scope → clarify or escalate | P2 | `tests/test_routing.py` |
| AC-05 | Maintain context in-session **and recall prior-session context on return** | P3 | `tests/test_memory_persistence.py` + `logs/memory_test.log` |
| AC-06 | Untrusted claimant input safe: injection + cross-claimant access **refused**; sensitive data never exposed in answers or logs | P3/P5 | `src/context/quarantine.py`, `src/guardrails/` |
| AC-07 | Machine-generated `logs/tool_calls.jsonl` from committed middleware; tool names reconcile with code | P4 | the JSONL + `src/observability/tool_logger.py` |
| AC-08 | `docs/failure-analysis.md` — **≥3 real failures from our own runs**, each citing Phoenix `run_id`+`span_id` (or tool-log record) + root cause + fix | P4 | the markdown + resolvable spans |
| AC-09 | Phoenix-derived golden-signals report (latency thinking/acting/tool, tokens, cost est., accuracy + hallucination rate) **and** cost/latency dashboard (screenshot + data file) | P5 | `reports/golden_signals.json`, `dashboard.png`, `dashboard_data.csv` |
| AC-10 | Input/output guardrails wired into the I/O path + machine-generated audit trail | P5 | `src/guardrails/`, `logs/agent_actions.jsonl` |
| AC-11 | Governance pack: risk register, model/system card, compliance mapping, output-risk classification — **each mitigation/claim citing a committed control** | P5 | `docs/*.md` |
| AC-12 | DeepEval report over a golden set (hallucination + faithfulness/relevance) + routing-logic, loop/cascade-guard, tool-contract tests | P6 | `reports/eval_report.json`, `tests/` |

### Non-functional

| ID | Requirement |
|---|---|
| NFR-01 | No secrets/keys committed; env-var config; committed `.env.example`; `.gitignore` covers `.env` |
| NFR-02 | **One** documented command runs the copilot; **a second** regenerates Phoenix traces + the evaluation; committed sample inputs |
| NFR-03 | Untrusted free-text claimant content is **quarantined and never treated as instructions** |
| NFR-04 | **Async** where the pipeline calls tools/models; graceful degradation on tool/model failure (timeouts, retries, exit conditions) |
| NFR-05 | All data synthetic; policy numbers + claimant identifiers **masked, never logged in plaintext** |
| NFR-06 | Evidence artifacts are machine-generated by committed code; the producing code is committed alongside |

---

## 5. The 6-Phase Implementation Plan

Each phase ends with a **commit gate**. Do not start phase N+1 until phase N's gate is green — the grader
scores artifacts, and half-finished surfaces score zero. Hour budgets are for the 20-hour engagement.

---

### Phase 1 — Foundation, Data & Secrets Hygiene  *(~2.5h)*
**Goal:** a runnable, Gemini-wired, secret-clean skeleton with committed synthetic data.

1. `requirements.txt` pinned to the §2 stack only. Python 3.11+ venv. **No Docker, no DB service.**
2. `.gitignore` covering `.env`, `*.sqlite*`, `__pycache__/`, `.chroma/`, `.phoenix/` — commit it **first**,
   before any `.env` can be created. `.env.example` listing every var (`GOOGLE_API_KEY`, model name,
   sqlite path, Phoenix endpoint, cost-per-token rates) with **no real values**. → NFR-01
3. `src/config.py` — `python-dotenv` + typed settings. Fail fast with a clear message if `GOOGLE_API_KEY`
   is absent. **Gemini is the only provider** — no other client is constructed anywhere. → R4
4. `scripts/generate_data.py` — synthetic FNOL claims (auto/property/liability; low/med/high value; a few
   with deliberate fraud indicators; a few with injection payloads embedded in free-text description) into
   `data/sample_claims/`, plus a synthetic policy-and-coverage corpus with numbered clauses into
   `data/policy_corpus/`. All identifiers synthetic + maskable. → R3
5. `src/main.py` — CLI skeleton (`run`, `trace`, `eval` subcommands) that loads a sample claim and prints
   a placeholder decision. Async-first from the start. → NFR-04
6. `README.md` stub with the two commands that will be true by Phase 6. → R5

**Gate:** `python -m src.main run --claim data/sample_claims/claim_001.json` executes; `git grep` finds no
secret; `data/` committed; no non-Gemini provider anywhere.

---

### Phase 2 — LangGraph Multi-Agent Core  *(~4h)*
**Goal:** the graded foundation in `src/graph.py` — the thing everything else instruments.

1. `src/state.py` — **typed** graph state (TypedDict/Pydantic): raw claim, quarantined claimant text,
   classification, severity, coverage decision + **cited clause id**, fraud indicators + risk, routing
   decision + rationale, escalation flag, step counter, messages.
2. `src/agents/supervisor.py` — supervisor node that reads state and routes FNOL work to workers.
   Must also satisfy **AC-04**: detect intent; ambiguous/out-of-scope → clarify or escalate, never mishandle.
3. **≥3 worker agents** — `classifier.py` (type + severity), `coverage.py` (policy check, **must cite the
   clause**), `fraud.py` (indicators + risk signal). Each returns **structured output** (Pydantic) at the
   node boundary. → AC-01, AC-02
4. `src/agents/router.py` — fast-track / standard / investigate **with rationale**. Hard rule:
   **suspected fraud OR high value ⇒ escalate to human, never auto-approve.** Encode as a deterministic
   guard, not a prompt suggestion — it is separately tested and audited. → AC-03
5. `src/graph.py` — assemble with **conditional edges**, a **checkpointer**
   (`langgraph-checkpoint-sqlite`), and a **recursion/step limit** (feeds `tests/test_loops.py`).
6. NFR-04: async tool/model calls, per-call timeout, bounded retry, and a degradation path that returns a
   partial decision + escalation rather than crashing.

**Gate:** end-to-end CLI run on 3+ sample claims produces a structured triage decision with clause
citation, fraud indicators, route and rationale; a high-value claim escalates; checkpoint file is written.

---

### Phase 3 — Context Engineering, Tiered Memory, MCP & Agentic RAG  *(~4h)*
**Goal:** the four remaining §7.1 foundation artifacts.

1. **Context engineering** `src/context/` — explicit, named implementations of **write / select / compress /
   isolate**, plus **summarization middleware** on long transcripts, plus **`quarantine.py`**: all
   claimant-supplied free text is wrapped as *data, never instructions*, tagged untrusted, and excluded from
   the instruction channel. → NFR-03, AC-06
2. **Tiered memory** `src/memory/` — short-term via the SqliteSaver checkpointer; long-term/semantic via
   **LangMem**. Write `tests/test_memory_persistence.py` proving **cross-session** recall: session A states
   a fact, process/thread ends, session B recalls it. Commit the run output to `logs/memory_test.log`
   produced by the test itself, not by hand. → AC-05, R1
3. **MCP server** `mcp_server/server.py` — MCP Python SDK over **stdio**, **≥2 tools + 1 resource**
   (e.g. tools: `lookup_policy`, `check_claim_history`; resource: the coverage-rules document). Consume it
   from the graph via **langchain-mcp-adapters** (`src/mcp_client.py`). Emit
   `logs/mcp_transcript.jsonl` from the client, machine-generated.
4. **Agentic RAG** `src/tools/rag_tool.py` — Chroma/FAISS + Sentence-Transformers over
   `data/policy_corpus/`. **Retrieval-in-the-loop**: the agent decides when to retrieve and may re-query;
   returns clause ids so `coverage.py` can cite them. → AC-01
5. Red-team the quarantine: injection payloads planted in Phase 1 must be **refused**, and a
   cross-claimant data request must be **refused**. → AC-06

**Gate:** MCP tools callable through the graph with a committed transcript; memory test passes across
sessions with a committed log; RAG returns citable clause ids; injection samples refused.

---

### Phase 4 — Arize Phoenix Observability  *(~3h)*
**Goal:** §7.2. Phoenix is mandated and is the single source for all later latency/token/cost evidence.

1. `src/observability/tracing.py` — Phoenix + `openinference-instrumentation-langchain` tracer
   **initialised and called in the run path** (imported-only will be marked down). Local, in-process,
   UI at `localhost:6006`.
2. `src/observability/tool_logger.py` — a logging **decorator/wrapper on every tool** (native and MCP)
   appending `{timestamp, agent, tool_name, args, result, latency_ms, status}` to `logs/tool_calls.jsonl`.
   Tool names **must reconcile** with the names in agent/MCP code. Args/results pass through PII masking
   before write. → AC-07, NFR-05
3. `scripts/export_traces.py` —
   `px.Client().get_spans_dataframe().to_parquet('traces/phoenix_spans.parquet')`.
   Export must contain **≥1 full run**, spans across **multiple agents + every tool call**, latencies present.
4. **Run the system enough to fail.** Collect **≥3 real failures** from our own runs (bad routing, tool
   timeout, hallucinated clause, loop, schema violation). Write `docs/failure-analysis.md` with, for each:
   Phoenix **`run_id` + `span_id`** (or a tool-log record), root cause, and the fix applied. → AC-08, R2
   Apply the fixes, then re-run and re-export so the committed trace reflects the fixed system.

**Gate:** `traces/phoenix_spans.parquet` committed with multi-agent + tool spans; `logs/tool_calls.jsonl`
non-trivial and name-reconciled; `docs/failure-analysis.md` has ≥3 failures whose cited ids **resolve in the
committed export**.

---

### Phase 5 — Cost Governance, Guardrails, Audit & Governance Pack  *(~3.5h)*
**Goal:** §7.3 + §7.4 + §7.5. Everything here must cite Phase 4 evidence.

1. **Golden signals** `scripts/golden_signals.py` — read Phoenix spans via `get_spans_dataframe()`;
   compute p50/p95 latency **by span type (thinking / acting / tool)**, token totals from LLM spans,
   **cost = tokens × configured Gemini price**; import **accuracy + hallucination rate from the eval report**
   (Phase 6 — run this script again after the eval so the field is populated, not null). Write
   `reports/golden_signals.json`. → AC-09
2. **Dashboard** `scripts/build_dashboard.py` → `reports/dashboard_data.csv` via
   `get_spans_dataframe().to_csv(...)`; screenshot the Phoenix UI latency/cost/token dashboard to
   `reports/dashboard.png`. **Both files are required** — the screenshot and the data it was drawn from. → AC-09
3. **Guardrails** `src/guardrails/` — Guardrails-AI / LLM Guard validators (or explicit policy functions)
   **wired into the graph's I/O nodes**, not sitting unused: input guard (injection, out-of-scope,
   cross-claimant access) and output guard (PII leakage, unsupported claim, auto-approval of an escalation
   case). Presidio-backed masking in `pii.py` applied to answers **and** logs. → AC-06, AC-10, NFR-05
4. **Audit trail** `src/observability/audit.py` — middleware appending
   `{actor, action, tool, decision, timestamp}` to `logs/agent_actions.jsonl` for every **consequential**
   action (coverage decision, fraud verdict, routing decision, escalation, refusal). → AC-10
5. **Governance pack** `docs/` — every entry **citing a committed control or artifact** (R2):
   - `risk-register.md` — risk, category (OWASP LLM Top 10 / NIST), likelihood, impact, mitigation
     (cite the committed control file), residual risk, owner.
   - `model-card.md` — model (**Gemini**), data (**synthetic**), intended use, limitations, known failure
     modes (**cite `failure-analysis.md`**), out-of-scope uses.
   - `compliance.md` — applicable **EU AI Act / NIST AI RMF / DPDP** obligations → how addressed →
     evidence artifact path.
   - `output-risk.md` — low/med/high output tiers, how high-risk is gated (human-in-the-loop / refusal),
     plus a **worked sample**.

**Gate:** guardrails demonstrably block on a red-team input (captured in the audit log); `logs/agent_actions.jsonl`
machine-generated; all four governance docs present with **resolving** citations.

---

### Phase 6 — Evaluation, Agent Tests, Reproducibility & Submission  *(~3h)*
**Goal:** §7.6 + §7.7, then close the loop on R1/R2/R5.

1. **Golden set** `data/golden_set.json` — claims with expected type, severity, coverage clause, fraud
   signal, route, and a grounded reference answer.
2. **Evaluation** `scripts/run_eval.py` — **DeepEval** with the **LLM-as-judge = Gemini** (R4) over the
   golden set: **hallucination + faithfulness/relevance**. Write `reports/eval_report.json`. Then
   **re-run `scripts/golden_signals.py`** so accuracy + hallucination rate land in the golden-signals file. → AC-09, AC-12
3. **Agent tests** (pytest, agent-specific — not generic volume):
   - `tests/test_routing.py` — asserts conditional edges route the right worker for given states, and that
     fraud/high-value states escalate. → AC-03, AC-04
   - `tests/test_loops.py` — asserts the max-steps / recursion limit stops a runaway loop.
   - `tests/test_tool_contracts.py` — asserts **each** tool's input/output schema **plus one error path**.
4. **Runbook** `README.md` — the **single command** that runs the copilot, and the **second command** that
   regenerates Phoenix traces **and** the evaluation; committed sample inputs listed; Phoenix setup
   (`localhost:6006`); how the dashboard screenshot is reproduced. → NFR-02, R5
5. **Final audit pass** — run `scripts/verify_citations.py`:
   - every path cited in `docs/*.md` exists;
   - every `run_id`/`span_id` cited resolves in `traces/phoenix_spans.parquet`;
   - every evidence file under `logs/ traces/ reports/` has a committed producer;
   - `git grep` finds no secret, no plaintext policy number / claimant id in any log;
   - no `openai`/`anthropic`/Docker artifacts.
6. Clean-clone rehearsal: fresh clone → `pip install -r requirements.txt` → `.env` from `.env.example` →
   both documented commands succeed. Then push to the assigned Virtusa GitLab project.

**Gate:** `pytest` green; `reports/eval_report.json` + populated `reports/golden_signals.json` committed;
citation verifier clean; clean-clone run reproduces traces and eval.

---

### Optional / bonus — only after Phase 6 is green
FastAPI streaming endpoint (`src/api/`); a demonstrated local run (screenshot/log); Presidio PII-redaction
before/after sample; a small red-team attack set + results; an optimization note with **measured**
before/after latency or cost from two Phoenix-derived reports.
**Never trade a required artifact for a bonus one.**

---

## 6. Repository, Version Control & Submission Rules

Straight from the business case. This project is **graded by reading the repository** — not by
watching it run — so version control is not housekeeping here, it is the delivery mechanism.

> ### ⚠️ It is **GitLab**, not GitHub
> The docx is explicit: *"Push the final repository to your assigned **Virtusa GitLab** project by
> the cut-off."* GitHub is never mentioned. Confirm the assigned GitLab project URL early and push
> there. A perfect repository on the wrong host scores nothing, and the cut-off is a hard deadline.

### 6.1 How the submission is evaluated

| | |
|---|---|
| Mode | **Automated review of the submitted Git repository** against the Hackathon Rubric — 7 categories / 100 marks |
| Basis | *"scored entirely from committed evidence in the repository"* |
| Live demo | **None.** *"No live demo judging."* Nothing you can only show in person counts. |
| Output | Per-team Excel report (Summary, Categories, Scorecard, Detailed, Improvement) |
| Bands | Pass ≥ 60 · Not Yet Passed < 60 |

The consequence worth internalising: **a reviewer who never speaks to us decides the grade from
`git clone` alone.** Anything that lives in a terminal scrollback, a local file, an uncommitted
notebook or a teammate's head is worth exactly zero.

### 6.2 What "committed" means in practice

The Evidence-in-Repo and Citation-Resolves rules (§1, R1 and R2) are repository rules. In commit terms:

1. **Only committed artifacts are scored.** A claim with no committed evidence scores **zero**.
2. **Evidence without producing code is heavily discounted** — *"a metric, trace or log a team could
   hand-write"*. So every JSONL, JSON, PNG, CSV and Parquet under `logs/`, `traces/` and `reports/`
   must be emitted by committed code, and **NFR-06** requires *"the code that produced each is
   committed alongside it"*. Commit the artifact and its producer **in the same commit** — it makes
   the pairing self-evident to a reviewer reading history.
3. **Every citation must resolve** to a committed artifact. A `run_id`/`span_id` in
   `docs/failure-analysis.md` that is not in the committed trace export counts as missing.
4. **Paths are graded.** §7 of the docx says each artifact *"must be present at (or near) the path
   shown"*. Use the exact paths in §3 of this file; do not reorganise them for taste.
5. **Committed sample inputs** are required (NFR-02, Rule R5) — `data/sample_claims/` is part of the
   deliverable, not test scaffolding.

### 6.3 What must NEVER be committed

| Never commit | Rule |
|---|---|
| Secrets, API keys, tokens — anywhere, including in a log, trace, notebook or fixture | NFR-01 |
| `.env` (a committed `.env.example` with no real values is required instead) | NFR-01 |
| Plaintext policy numbers or claimant identifiers, in **any** file including logs | R3, NFR-05 |
| Real (non-synthetic) claim or policy data | R3 |

`.gitignore` must cover `.env` — that is itself a graded artifact (§7.4 "Secrets hygiene"). A key
committed once stays in the history even after deletion: if it happens, **rotate the key**, do not
merely delete the file.

### 6.4 The `.gitignore` trap — do not ignore the evidence

Normal engineering instinct is to gitignore `logs/`, `reports/`, `*.png`, `*.parquet` and anything
generated. **Here that instinct destroys the grade.** Those directories *are* the deliverable.

| Must be committed (never ignore) | Must stay ignored (regenerable, not evidence) |
|---|---|
| `logs/*.jsonl`, `logs/*.log` | `.env` |
| `traces/phoenix_spans.parquet` | `var/` — SQLite checkpoints, memory store, Chroma index |
| `reports/*.json`, `reports/*.png`, `reports/*.csv` | `runs/` — per-run decision dumps |
| `data/sample_claims/`, `data/policy_corpus/` | `__pycache__/`, `.venv/`, `.pytest_cache/` |
| `docs/*.md` | `.phoenix/`, `phoenix.db` |

The line to hold: **generated-and-graded is committed; generated-and-regenerable is ignored.** The
current `.gitignore` is already correct on this — check it before adding any new pattern, and never
add a blanket `*.log`, `*.json`, `*.png` or `logs/` rule.

### 6.5 Commit discipline for this project

- **Commit at every phase gate** (§5), with the artifact paths named in the commit message. The
  reviewer reads history as well as the tree; a message naming `logs/tool_calls.jsonl` and
  `src/observability/tool_logger.py` together demonstrates the R1 pairing at a glance.
- **Work on a branch and merge with `--no-ff`** — never commit straight to `main`. The merge commits
  are themselves scored evidence; see §7 for the rule and the required count.
- **Do not rewrite history** (`--amend`, force-push, squash) once teammates have pulled — this is a
  shared repo with phases owned by different people (AGENTS.md §3). Squashing also destroys the
  `--no-ff` merge commits that §7 requires.
- **Never commit on behalf of the user without being asked.** Commits and pushes are the user's call.
- **Regenerate before the final push**, so committed evidence reflects the committed code — not an
  earlier version of it. In particular, Phase 4 must re-export traces *after* its fixes land
  (SPEC-07 §2.4), and Phase 5's golden-signals report must be re-run *after* the Phase 6 eval
  (SPEC-08 §2.1).

### 6.6 Pre-submission checklist

Run before the final push to the assigned Virtusa GitLab project:

```bash
python -m src.main verify                 # citation + hygiene verifier (SPEC-12 §2.4)
python -m src.main data --check           # corpus regenerates byte-identically
pytest -q                                 # agent tests green
git status --short                        # nothing graded left untracked
git ls-files logs traces reports docs     # every evidence artifact is actually tracked
```

- [ ] Clean-clone rehearsal: fresh `git clone` → `pip install -r requirements.txt` →
      `cp .env.example .env` + key → **both** documented commands succeed (NFR-02, Rule R5).
- [ ] No secret anywhere in the tree **or in the history**.
- [ ] Every artifact in §4 present at its graded path, produced by committed code.
- [ ] Every citation in `docs/` resolves.
- [ ] `git log --merges --oneline | wc -l` returns **≥ 3** — the PR-driven merges required by §7.
- [ ] No commit landed directly on `main` (§7 rule 1).
- [ ] Pushed to the assigned **Virtusa GitLab** project before the cut-off.

---

## 7. Git (Section 8.1 and Section 2 of the brief)

The brief is explicit, and these are scored deterministically:

> **PR-driven Git history: at least 3 PR-driven merges (`git merge --no-ff`); no direct pushes to
> `main`.**
> **Submission:** push final code to your assigned Virtusa GitLab repository by the cohort cut-off.
> **Evaluation Mode:** automated static review of the submitted Git repository.

### Rules

1. **Never commit directly to `main`.** Every change lands through a branch and a `--no-ff` merge,
   so the merge commit survives as evidence of the PR. A fast-forward merge erases it.
2. **At least three PR-driven merges** must exist in the history. Group work into coherent
   branches (e.g. graph + state, MCP + tools, memory + context, evidence + docs) — not three
   token merges made to satisfy a counter.
3. **Never commit `.env`.** It is git-ignored; `make audit` scans every tracked file for
   key-shaped strings. `.env.example` *is* committed (NFR-01).
4. The remote is **Virtusa GitLab**, not GitHub. Do not add GitHub-specific workflow files or
   assume `gh`.

### Working shape

```bash
git checkout -b phase/<n>-<slug>          # branch per coherent unit of work
# ... commit as you go ...
git checkout main
git merge --no-ff phase/<n>-<slug> -m "Merge: <what landed>"
```

Verify the count before submitting — merge commits are what the static review reads:

```bash
git log --merges --oneline | wc -l        # must be >= 3
git log --graph --oneline --all           # the history a reviewer sees
```

---

## 8. Working Guidelines for Claude in this repo

- **Read this file and the docx before proposing anything.** The rubric is the spec.
- **Then read the map.** [`AGENTS.md`](AGENTS.md) is the table of contents, [`design.md`](design.md) is the
  architecture, [`specs/`](specs/) holds the per-component contracts. Conflict order:
  **docx > CLAUDE.md > design.md > specs/ > code.**
- **Frozen after P3:** `TriageState` field names, graph node names, tool names, `emit_audit()` and
  `logged_tool()` signatures. Phases 4–6 reconcile their evidence against these — changing one is a
  cross-phase breaking change, not a refactor.
- **Artifact-first.** For any task, name the graded artifact path it produces before writing code. If a
  change produces no committed artifact and supports none, question whether it belongs in a 20-hour build.
- **Never hand-write evidence.** No fabricated span ids, log lines, metrics, or screenshots. If evidence is
  missing, the fix is to *run the system and export*, never to author the file. (R1)
- **Never cite what doesn't exist.** Before writing a citation into a doc, confirm the artifact is committed. (R2)
- **Gemini only.** Do not introduce any other model provider, including for evaluation or embeddings
  fallbacks. Embeddings are local Sentence-Transformers. (R4)
- **No Docker, no external DB.** SQLite file + local Chroma/FAISS only. (R4)
- **Synthetic + masked.** No realistic-looking real-world identifiers; mask before any log write. (R3, NFR-05)
- **Quarantine claimant text everywhere.** Any new path that touches claimant free text routes through
  `src/context/quarantine.py`. (NFR-03)
- **Async + degrade gracefully.** Tool/model calls are async with timeout, bounded retry, and an exit
  condition. Failure returns a partial decision + escalation, never an unhandled exception. (NFR-04)
- **Escalation is a hard rule, not a prompt.** Suspected fraud or high value never auto-approves — enforce
  in code, assert in tests, record in the audit log. (AC-03)
- **Structured output at node boundaries.** Pydantic models, not free-form strings, between agents.
- **Keep the traceability table in §4 true.** When a phase lands, verify its ACs still map to real paths.
- **Commit at every phase gate**, with the artifact paths named in the commit message.
- Scope discipline: no containerization, no real system integrations, no UI polish, no generic unit-test
  padding. (§6.2)
