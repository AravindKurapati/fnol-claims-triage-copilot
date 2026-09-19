# Model Card — FNOL Claims-Triage Copilot

**AC-11 · SPEC-10 §2.2.** Every statement cites the committed control or artifact behind it.
`scripts/verify_citations.py` resolves each citation (Rule R2).

## 1. System and models

| Item | Value | Source |
|---|---|---|
| System | LangGraph multi-agent triage copilot: a supervisor, three workers (classifier, coverage, fraud), a deterministic router, and input/output guard nodes | `src/graph.py`, `src/agents/` |
| Agent model | Google **Gemini `gemini-3.5-flash-lite`**, temperature 0.1, structured (Pydantic) output at every node | `src/config.py` (`GEMINI_MODEL`, `GEMINI_TEMPERATURE`), `src/llm.py::structured` |
| Evaluation judge | Google **Gemini `gemini-3.1-flash-lite`** (DeepEval LLM-as-judge) | `src/config.py` (`GEMINI_JUDGE_MODEL`), `scripts/run_eval.py` |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2`, run locally | `src/config.py` (`EMBEDDING_MODEL`), `src/tools/rag_tool.py::build_index` |
| PII detection | Microsoft Presidio with a small spaCy NER model | `src/guardrails/pii.py::redact` |
| Provider rule | Gemini is the only model provider (Rule R4). A non-Gemini key in the environment fails startup. | `src/config.py`, verifier check 6 in `scripts/verify_citations.py` |

The model ids are configuration. The agents run on a flash-lite model because free-tier quotas are
per model and the batch exceeded the larger model's 5 requests/minute (`docs/failure-analysis.md` F-03).

The **decisions that carry consequences are not made by the model.** Queue selection, escalation
and the auto-approval bar are deterministic code in `src/agents/router.py::decide_route`, and the
model's outputs are inputs to that code. The model is used for intent, classification, the coverage
reading of retrieved clauses, and fraud-evidence extraction.

## 2. Data

- **Synthetic only (Rule R3).** Claims, policies, claim history and the clause-numbered policy corpus
  are generated deterministically by `scripts/generate_data.py` (fixed seed `DATA_SEED` in
  `src/config.py`). They are committed under `data/policy_corpus/` and `data/sample_claims/`.
  No real person, policy or claim is represented.
- **Twelve sample claims** cover an authored scenario matrix: clean fast-track, clean standard,
  high-value escalation, high and medium fraud, a not-covered exclusion, a lapsed policy, an
  ambiguous/out-of-scope request, prompt injection and cross-claimant access (`data/sample_claims/`).
- **Golden set** for evaluation: `data/golden_set.json`, built from the generator's oracle by
  `scripts/build_golden_set.py`.
- **No training or fine-tuning.** The model is used as-is through the API. Nothing about claimants is
  sent to the provider except the quarantined, masked claim context
  (`src/context/quarantine.py::render_for_prompt`, `src/security/masking.py::mask_text`).

## 3. Intended use

Decision **support** for a first-notice-of-loss handler: a proposed claim type and severity, a
coverage reading **citing the clause applied**, fraud indicators, and a proposed queue with its
rationale. Every output is a recommendation, and the terminal message says so. Suspected fraud, high
value (≥ ₹500,000, `HIGH_VALUE_THRESHOLD`), ambiguous coverage and any degraded run are always
handed to a human (`src/agents/router.py::decide_route`,
`tests/test_routing.py::test_high_value_is_escalated_regardless_of_fraud_risk`).

## 4. Out-of-scope uses

- **Autonomous settlement or denial.** The system never pays or refuses a claim. An escalated claim
  cannot be auto-approved (OG-04, `src/models.py::RoutingDecision`).
- **Real personal data or production claims data.** The masking and PII controls are built for
  synthetic identifiers and have not been validated on real data.
- **Policy servicing, quotes or general Q&A.** These are refused or redirected by IG-03
  (`src/guardrails/input_guard.py::check_out_of_scope`).
- **Any line of business outside auto, home and general liability** (`data/policy_corpus/`).

## 5. Known failure modes

All seven were found in live traced runs and are documented with run and span ids in
`docs/failure-analysis.md`.

| ID | Failure mode | Status |
|---|---|---|
| F-01 | The synchronous checkpointer crashed every async run | fixed; regression test |
| F-02 | A retired model id was retried, then the claim was bounced back to the claimant | fixed; fails fast and escalates |
| F-03 | The free-tier rate limit degraded 8 of 12 claims | fixed; retry-after and pacing |
| F-04 | Circumstance-based exclusions (racing) were missed because the coverage agent never saw the narrative | fixed; fenced narrative in the coverage view |
| F-05 | Label drift ("property: burglary") silently disabled fraud indicators | fixed; labels validated in code |
| F-06 | Plaintext identifiers in trace spans | fixed; masking span exporter |
| F-07 | The PII guard redacted "Queue" as a person name | fixed; guard scoped to model text |

**Limitations that remain:**

- A retrieved clause can be cited and still not apply. OG-03 only checks that the clause was
  retrieved, so this is measured by clause accuracy in `reports/eval_report.json`, not blocked.
- The fraud threshold disagrees with the business oracle on one scenario (open finding O-01 in
  `docs/failure-analysis.md`). It is reported, not tuned away.
- The injection and cross-claimant guards are pattern-based (`src/guardrails/input_guard.py`). A
  novel paraphrase can pass IG-01. Quarantine (`src/context/quarantine.py`) is the defence that does
  not depend on detection.
- Presidio's small NER model both over-redacts (F-07) and can miss names. OG-02 is best effort.
- The evaluation set is 12 authored claims. That is enough to find defects, but not to estimate
  accuracy with a tight interval.

## 6. Evaluation summary

Source: `reports/eval_report.json` (`scripts/run_eval.py`). It covers all 12 golden cases from the
final traced run `run-28184ad47492`, whose spans are in `traces/phoenix_spans.parquet`. The judge is
Gemini `gemini-3.1-flash-lite` via DeepEval, which scored 10 triage cases in 90 judge calls.

| Metric | Value | Gate |
|---|---:|---|
| Escalation recall (a missed escalation is the worst failure) | **1.00** | must be 1.0 ✅ |
| Escalated-and-auto-approved decisions | **0** | must be 0 ✅ |
| Citation validity (cited clause exists in the corpus) | **1.00** | must be 1.0 ✅ |
| Routing accuracy (queue, or clarify/refuse outcome) | 0.917 (11/12) | — |
| Clause / coverage status / claim type / severity / fraud risk accuracy | 1.00 each (10 triage cases) | — |
| Adversarial handling (injection, cross-claimant, out-of-scope) | 1.00 (3/3) | — |
| Hallucination rate (DeepEval, lower is better) | 0.041 | — |
| Faithfulness to retrieved clauses (DeepEval) | 0.978 | — |
| Answer relevancy (DeepEval) | 0.891 | — |

The one routing miss is claim 007, which is the open product decision O-01 in
`docs/failure-analysis.md`, not a model error. The router applies SPEC-02's "≥ 3 indicators →
investigate" rule, and the oracle expects `standard`.

Operational signals for the same run come from `reports/golden_signals.json`:

- **Cost:** 37,082 tokens and **$0.0171** per 12-claim batch (≈ $0.0014 per claim) at the price
  basis in `src/config.py`.
- **Latency:** p50 19.9 s and p95 28.4 s per claim end to end. This is dominated by free-tier pacing
  of the model calls. The tool p50 is 15.5 ms (`reports/optimization_note.md`).
- **Errors:** zero error spans.

The red-team suite passes 16/16 (`reports/redteam_results.json`).

