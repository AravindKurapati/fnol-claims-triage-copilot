# CLAUDE.md — FNOL Claims-Triage Copilot

Authoritative working agreement for this repository. It is derived **verbatim in intent** from
`AAIE_HACK_06_INS_FNOL_Claims_Triage_Copilot.docx` (Business Case **BC-AAIE-HACK-06**).
If anything in this file conflicts with that docx, **the docx wins** — fix this file, don't drift.

---

## 0. Project Identity

| Field | Value |
|---|---|
| Title · ID | FNOL Claims-Triage Copilot · **BC-AAIE-HACK-06** · Insurance |
| Engagement | Agentic AI Engineer Pathway capstone · 20 hours · team of 2–4 |
| Evaluation | **Automated review of the committed Git repository**, 7 categories / 100 marks. **No live demo.** |
| Submission | Push to the assigned **Virtusa GitLab** project before cut-off |
| Pass band | ≥ 60 Pass · < 60 Not Yet Passed |

**Problem.** At FNOL a handler must classify the claim's type and severity, check coverage against the
policy, screen for fraud, and route it (fast-track / standard / investigate). Done manually this is slow and
inconsistent, and simple claims wait behind complex ones. We build a copilot that triages a new claim
end-to-end and drafts the routing decision **with an audit trail**.

**Scored:** a LangGraph multi-agent system (context engineering, tiered memory, a custom MCP server, an
agentic-RAG tool) that is then **instrumented and governed** — Phoenix observability, cost/latency
governance, guardrails and audit, governance docs, agent-level evaluation and tests.
**Not scored:** UI polish, generic unit-test volume, deployment path. Containerized/cloud deployment is
**out of scope for this cut**.

---

## 1. Non-Negotiable Rules

The rules the grader applies. Build-time constraints, not guidelines.

**R1 — Evidence-in-Repo.** Only committed artifacts are scored. A claim with no committed evidence scores
**zero**; evidence with no producing code (*"a metric, trace or log a team could hand-write"*) is **heavily
discounted**.
⇒ Every file under `logs/`, `traces/`, `reports/` must be emitted by committed code. Never hand-author evidence.

**R2 — Citation-Resolves.** Every citation in a document (a Phoenix `run_id`/`span_id`, a log record, a
control file) must resolve to a committed artifact, or it counts as missing.
⇒ Verify before committing any doc; `scripts/verify_citations.py` automates it (Phase 6).

**R3 — Synthetic-Data.** Synthetic claims and policies only. Policy numbers and claimant identifiers are
synthetic **and masked where shown**, and **never written to logs in plaintext**.

**R4 — Open-Source & Gemini-Only.** **Gemini is the only model provider — not Claude, not OpenAI.** pip +
Python; **no Docker, no external database service.**
⇒ No other-provider import or direct dependency anywhere. DeepEval's judge must also be Gemini.

**R5 — Reproducibility.** The system, its traces and its evaluation regenerate from **a single documented
command** with **committed sample inputs**.

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

Paths are graded — put artifacts **at these paths**.

```
CLAUDE.md  AGENTS.md  design.md  specs/  README.md      # rules · map · architecture · contracts · runbook
.env.example  .gitignore  requirements.txt              # NFR-01: no real values; .gitignore covers .env

src/
  main.py            CLI entrypoint — the required interface
  config.py          model, thresholds, prices, paths      resilience.py   timeout/retry/degrade (NFR-04)
  graph.py           7.1 — supervisor + ≥3 workers, conditional edges, checkpointer, structured output
  state.py           typed graph state
  agents/            supervisor · classifier (AC-01) · coverage (AC-01, cites the clause)
                     fraud (AC-02) · router (AC-03, queue + HITL escalation)
  context/           7.1 — strategies · summarization · quarantine (NFR-03, AC-06)
  memory/            7.1 — short_term (checkpointer) · long_term (cross-session)
  tools/             rag_tool (7.1) · registry (registration + logging decorator)
  mcp_client.py      langchain-mcp-adapters
  observability/     tracing (7.2 — CALLED, not just imported) · tool_logger (AC-07) · audit (AC-10)
  security/          masking.py (NFR-05)
  guardrails/        7.4 — input_guard · output_guard · pii (Presidio), wired into the I/O path
  api/               OPTIONAL bonus — FastAPI streaming

mcp_server/          7.1 — ≥2 tools + 1 resource, stdio
data/                policy_corpus/ · sample_claims/ (NFR-02, R5) · golden_set.json
scripts/             generate_data (R3) · export_traces · golden_signals · build_dashboard
                     run_eval (DeepEval, Gemini judge) · verify_citations (R2)
tests/               test_memory_persistence (AC-05) · test_routing · test_loops · test_tool_contracts (AC-12)

── committed evidence ───────────────────────────────────────────────────────────
logs/                quarantine_events · tool_calls (AC-07) · agent_actions (AC-10)
                     mcp_transcript (7.1) · memory_test.log (7.1)
traces/              phoenix_spans.parquet — ≥1 full run, all agents + every tool call
reports/             golden_signals.json · dashboard.png + dashboard_data.csv (AC-09) · eval_report.json (AC-12)
docs/                failure-analysis (AC-08) · risk-register · model-card · compliance · output-risk (AC-11)

── gitignored (regenerable) ─────────────────────────────────────────────────────
var/                 checkpoints · memory DB · vector index          runs/   per-run decisions
```

---

## 4. Acceptance Criteria — traceability

Every AC maps to committed code **and** committed evidence. Keep this table true.

| ID | Criterion | Phase | Evidence |
|---|---|---|---|
| AC-01 | Classify type + severity; check coverage, **citing the clause applied** | P2/P3 | run output + `traces/` |
| AC-02 | Fraud-risk signal **with the indicators that triggered it** | P2 | run output + `traces/` |
| AC-03 | Route with rationale; **suspected fraud or high value ⇒ human, never auto-approved** | P2 | `logs/agent_actions.jsonl` |
| AC-04 | Identify intent; ambiguous/out-of-scope clarified or escalated, not mishandled | P2 | `tests/test_routing.py` |
| AC-05 | Uses in-session facts **and recalls prior-session context** | P3 | `tests/test_memory_persistence.py` + `logs/memory_test.log` |
| AC-06 | Injection and cross-claimant access refused; sensitive data never exposed | P3/P5 | `src/context/quarantine.py`, `src/guardrails/` |
| AC-07 | Machine-generated tool log from committed middleware; names reconcile with code | P4 | `logs/tool_calls.jsonl` + producer |
| AC-08 | **≥3 real failures**, each citing Phoenix `run_id` + `span_id`, root cause, fix | P4 | `docs/failure-analysis.md` |
| AC-09 | Golden signals (latency by thinking/acting/tool, tokens, cost, accuracy, hallucination) + dashboard | P5 | `reports/golden_signals.json`, `dashboard.png`, `dashboard_data.csv` |
| AC-10 | I/O guardrails wired into the path + machine-generated audit trail | P5 | `src/guardrails/`, `logs/agent_actions.jsonl` |
| AC-11 | Governance pack — **each claim citing a committed control** | P5 | `docs/*.md` |
| AC-12 | DeepEval over a golden set + routing / loop / tool-contract tests | P6 | `reports/eval_report.json`, `tests/` |

**NFRs.** `01` no secrets committed, `.env.example` + `.gitignore` covering `.env` · `02` one command runs
it, a second regenerates traces + eval, committed inputs · `03` untrusted text quarantined, never treated as
instructions · `04` async tool/model calls, graceful degradation (timeouts, retries, exit conditions) ·
`05` all data synthetic, identifiers masked and never logged in plaintext · `06` evidence machine-generated
by committed code, producer committed alongside.

---

## 5. The 6-Phase Implementation Plan

Each phase ends with a **gate**. Don't start phase N+1 until N's gate is green — the grader scores
artifacts, and half-finished surfaces score zero. Hours are for the 20-hour engagement. Component detail
lives in `specs/`.

### Phase 1 — Foundation, Data & Secrets Hygiene  *(~2.5h · SPEC-01)*

1. `requirements.txt` pinned to the §2 stack only. Python 3.11+. No Docker, no DB service.
2. `.gitignore` covering `.env` committed **first**; `.env.example` listing every var with no real values (NFR-01).
3. `src/config.py` — dotenv + typed settings, fail fast without `GOOGLE_API_KEY`, Rule R4 provider guard.
4. `scripts/generate_data.py` — deterministic synthetic FNOLs (incl. fraud indicators and injection
   payloads) and a clause-numbered policy corpus. Identifiers synthetic and maskable (R3).
5. `src/main.py` — CLI skeleton (`run`, `trace`, `eval`), async-first (NFR-04).
6. `README.md` stub with the two commands Phase 6 makes true (R5).

**Gate:** a claim runs through the CLI; no secret committed; `data/` committed; no non-Gemini provider.

### Phase 2 — LangGraph Multi-Agent Core  *(~4h · SPEC-02)*

1. `src/state.py` — typed state: claim, quarantined text, classification, coverage + **cited clause**,
   fraud indicators, routing + rationale, escalation flag, step counter.
2. `supervisor.py` — routes FNOL work to workers; detects intent, so ambiguous/out-of-scope **clarifies or
   escalates, never mishandles** (AC-04).
3. **≥3 workers** — `classifier.py` (type + severity), `coverage.py` (**must cite the clause**),
   `fraud.py` (indicators + risk). Pydantic **structured output** at every node boundary (AC-01, AC-02).
4. `router.py` — fast-track / standard / investigate **with rationale**. Hard rule: **suspected fraud or
   high value ⇒ escalate to a human, never auto-approve.** A deterministic guard, not a prompt (AC-03).
5. `src/graph.py` — conditional edges, `langgraph-checkpoint-sqlite`, recursion/step limit.
6. NFR-04: async calls, timeouts, bounded retry, and degradation that returns a partial decision +
   escalation rather than crashing.

**Gate:** end-to-end run produces a structured decision with clause citation, fraud indicators, route and
rationale; a high-value claim escalates; a checkpoint row exists.

### Phase 3 — Context, Memory, MCP & Agentic RAG  *(~4h · SPEC-03…06)*

1. **Context engineering** — named **write / select / compress / isolate**, summarization middleware, and
   `quarantine.py`: claimant free text is *data, never instruction* (NFR-03, AC-06).
2. **Tiered memory** — SqliteSaver short-term + LangMem long-term. `tests/test_memory_persistence.py`
   proves **cross-session** recall and writes `logs/memory_test.log` itself (AC-05, R1).
3. **MCP server** — MCP Python SDK over stdio, **≥2 tools + 1 resource**, consumed via
   **langchain-mcp-adapters**; client emits `logs/mcp_transcript.jsonl`.
4. **Agentic RAG** — Chroma/FAISS + Sentence-Transformers over `data/policy_corpus/`, retrieval-in-the-loop,
   returning clause ids for `coverage.py` to cite (AC-01).
5. Red-team the quarantine: planted injections **refused**; cross-claimant requests **refused** (AC-06).

**Gate:** MCP callable through the graph with a committed transcript; memory test passes across sessions;
RAG returns citable clause ids; injection samples refused.

### Phase 4 — Arize Phoenix Observability  *(~3h · SPEC-07)*

1. `tracing.py` — Phoenix + `openinference-instrumentation-langchain` **initialised and called in the run
   path** (imported-only is marked down). Local, UI at `localhost:6006`.
2. `tool_logger.py` — a decorator on **every** tool appending
   `{timestamp, agent, tool_name, args, result, latency_ms, status}`. Names **must reconcile** with the
   code; args/results masked (AC-07, NFR-05).
3. `scripts/export_traces.py` → `traces/phoenix_spans.parquet` — ≥1 full run, spans across **multiple
   agents + every tool call**, latencies present.
4. **Run the system enough to fail.** Write `docs/failure-analysis.md` with **≥3 real failures**, each
   citing Phoenix `run_id` + `span_id`, root cause and fix. Apply the fixes, then **re-export** (AC-08, R2).

**Gate:** parquet committed with multi-agent + tool spans; tool log non-trivial and name-reconciled; every
cited id resolves in the committed export.

### Phase 5 — Cost Governance, Guardrails, Audit & Governance  *(~3.5h · SPEC-08…10)*

1. **Golden signals** — read Phoenix spans; p50/p95 latency **by thinking/acting/tool**, token totals,
   **cost = tokens × price**, plus accuracy + hallucination rate **from the eval report**. Re-run after
   Phase 6 or those fields stay null (AC-09).
2. **Dashboard** — `reports/dashboard.png` (Phoenix UI) **and** `reports/dashboard_data.csv`. Both required.
3. **Guardrails** — input (injection, out-of-scope, cross-claimant) and output (PII leak, unsupported claim,
   auto-approval of an escalated case) **wired into the graph's I/O nodes**, plus Presidio masking (AC-06, AC-10).
4. **Audit trail** — `{actor, action, tool, decision, timestamp}` to `logs/agent_actions.jsonl` for every
   consequential action (AC-10).
5. **Governance pack** — `risk-register.md`, `model-card.md`, `compliance.md` (EU AI Act / NIST AI RMF /
   DPDP), `output-risk.md`. **Every claim cites a committed control** (AC-11, R2).

**Gate:** guardrails block a red-team input and the block appears in the audit log; all four docs present
with resolving citations.

### Phase 6 — Evaluation, Testing, Reproducibility & Submission  *(~3h · SPEC-11, 12)*

1. **Golden set** `data/golden_set.json` — expected type, severity, clause, fraud signal, route.
2. **Evaluation** — DeepEval with **judge = Gemini** (R4): hallucination + faithfulness/relevance →
   `reports/eval_report.json`. Then **re-run golden signals** so AC-09's quality fields populate.
3. **Agent tests** — `test_routing.py` (right worker per state; fraud/high-value escalate),
   `test_loops.py` (step limit stops runaway loops), `test_tool_contracts.py` (each tool's schema + one
   error path) (AC-12).
4. **Runbook** `README.md` — the single run command, the trace + eval regeneration command, committed
   sample inputs, Phoenix setup (NFR-02, R5).
5. **Final audit** — `scripts/verify_citations.py`: cited paths exist, span ids resolve, every evidence
   file has a producer, no secret, no plaintext identifier in logs, no disallowed provider or Docker.
6. **Clean-clone rehearsal**, then push to the assigned Virtusa GitLab project.

**Gate:** `pytest` green; eval report and populated golden signals committed; verifier clean; clean clone
reproduces traces and eval.

### Optional — only once Phase 6 is green
FastAPI streaming endpoint; a demonstrated local run; Presidio before/after sample; a red-team attack set +
results; an optimisation note with a **measured** before/after from two Phoenix-derived reports.
**Never trade a required artifact for a bonus one.**

---

## 6. Repository & Submission Rules

This project is **graded by reading the repository**, not by watching it run. Version control is the
delivery mechanism, not housekeeping.

> ### ⚠️ It is **GitLab**, not GitHub
> The docx: *"Push the final repository to your assigned **Virtusa GitLab** project by the cut-off."*
> GitHub is never mentioned. A perfect repository on the wrong host scores nothing.

**How it's judged.** Automated review of the submitted Git repository — 7 categories / 100 marks, *"scored
entirely from committed evidence"*, **no live demo**, per-team Excel report, pass ≥ 60. A reviewer who never
speaks to us decides the grade from `git clone` alone: anything in a terminal scrollback, a local file or a
teammate's head is worth zero.

### 6.1 What "committed" means

1. **Only committed artifacts score.** No committed evidence ⇒ **zero**.
2. **Evidence without producing code is heavily discounted.** NFR-06 requires *"the code that produced each
   is committed alongside it"* — commit artifact and producer in the **same commit** so the pairing is
   visible in history.
3. **Every citation must resolve.** A `run_id`/`span_id` not in the committed trace counts as missing.
4. **Paths are graded** — *"present at (or near) the path shown"*. Use §3; don't reorganise for taste.
5. **Committed sample inputs** are part of the deliverable (NFR-02, R5).

### 6.2 Never commit

Secrets or API keys anywhere (including inside a log, trace or fixture) · `.env` (commit `.env.example`
with no real values) · plaintext policy numbers or claimant identifiers in **any** file · real non-synthetic
data. `.gitignore` covering `.env` is itself a graded artifact (§7.4 of the docx). A key committed once
survives deletion — if it happens, **rotate it**.

### 6.3 The `.gitignore` trap

Instinct says to ignore `logs/`, `reports/`, `*.png`, `*.parquet`. **Here that destroys the grade** — those
directories *are* the deliverable.

| Commit (never ignore) | Ignore (regenerable, not evidence) |
|---|---|
| `logs/*.jsonl`, `logs/*.log` | `.env` |
| `traces/phoenix_spans.parquet` | `var/` — checkpoints, memory DB, vector index |
| `reports/*.json`, `*.png`, `*.csv` | `runs/` — per-run decision dumps |
| `data/`, `docs/*.md` | `__pycache__/`, `.venv/`, `.phoenix/`, `.deepeval/` |

**Generated-and-graded is committed; generated-and-regenerable is ignored.** Never add a blanket `*.log`,
`*.json`, `*.png` or `logs/` rule.

### 6.4 Discipline

- **Commit at every phase gate**, naming the artifact paths in the message.
- **Work on a branch and merge with `--no-ff`** — never commit straight to `main`. See §7.
- **Don't rewrite history** (`--amend`, force-push, squash) once teammates have pulled — squashing also
  destroys the `--no-ff` merge commits §7 requires.
- **Never commit or push on the user's behalf without being asked.**
- **Regenerate before the final push** so evidence reflects the committed code: Phase 4 re-exports traces
  after its fixes land; Phase 5 re-runs golden signals after the Phase 6 eval.

### 6.5 Pre-submission checklist

```bash
python -m src.main verify        # citation + hygiene verifier
python -m src.main data --check  # corpus regenerates byte-identically
pytest -q                        # agent tests green
git status --short               # nothing graded left untracked
git log --merges --oneline       # >= 3 PR-driven merges (§7)
```

- [ ] Clean-clone rehearsal: `git clone` → `pip install -r requirements.txt` → `.env` + key → **both**
      documented commands succeed (NFR-02, R5).
- [ ] No secret in the tree **or the history**; every §4 artifact at its graded path with its producer.
- [ ] Every citation in `docs/` resolves; no commit landed directly on `main`.
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
- **Then the map:** [`AGENTS.md`](AGENTS.md) is the table of contents, [`design.md`](design.md) the
  architecture, [`specs/`](specs/) the per-component contracts. Conflict order:
  **docx > CLAUDE.md > design.md > specs/ > code.**
- **Artifact-first.** Name the graded artifact path a task produces before writing code. If a change
  produces and supports none, question whether it belongs in a 20-hour build.
- **Never hand-write evidence** and **never cite what doesn't exist.** Missing evidence is fixed by running
  the system and exporting, never by authoring the file (R1, R2).
- **Gemini only** — including for evaluation. Embeddings are local Sentence-Transformers. **No Docker, no
  external DB** (R4).
- **Synthetic and masked.** Mask before any log write (R3, NFR-05).
- **Quarantine claimant text everywhere** — any new path touching free text goes through
  `src/context/quarantine.py` (NFR-03).
- **Async + degrade gracefully.** Failure returns a partial decision + escalation, never an unhandled
  exception (NFR-04).
- **Escalation is a hard rule, not a prompt** — enforce in code, assert in tests, record in the audit log (AC-03).
- **Structured output at node boundaries.** Pydantic models, not free-form strings.
- **Frozen after P3:** `TriageState` field names, graph node names, tool names, `emit_audit()` and
  `logged_tool()` signatures. Phases 4–6 reconcile evidence against these — changing one is a cross-phase
  breaking change, not a refactor.
- **Keep §4 true**, and commit at every phase gate with artifact paths in the message.
- **Scope discipline:** no containerization, no real integrations, no UI polish, no unit-test padding.
