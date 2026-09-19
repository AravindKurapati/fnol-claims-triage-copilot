# Compliance Mapping — FNOL Claims-Triage Copilot

**AC-11 · SPEC-10 §2.3.** One row per obligation: **obligation → how it is addressed → evidence
artifact**. Every evidence cell is a committed path that `scripts/verify_citations.py` resolves
(Rule R2). This is an engineering mapping for a synthetic-data capstone, not a legal opinion.

## 1. EU AI Act

**Risk classification.** Annex III §5(c) lists AI used for risk assessment and pricing in *life and
health* insurance as high-risk. This copilot triages property and casualty FNOL claims (auto, home,
general liability), so it falls outside that listing. It does, however, inform decisions about
individuals' claims. We therefore apply the high-risk controls of Articles 12–15 **voluntarily**,
and treat the system as decision support that is never autonomous.

| Obligation | How addressed | Evidence |
|---|---|---|
| Risk management (Art. 9) | A maintained risk register with a committed control and evidence for each risk | `docs/risk-register.md` |
| Data governance (Art. 10) | Synthetic data only, generated deterministically and checked for byte-identical regeneration | `scripts/generate_data.py`, `data/policy_corpus/`, `data/sample_claims/` |
| Record-keeping / logging (Art. 12) | Every consequential action is logged as `{actor, action, tool, decision, timestamp}` by one function, with every tool call logged by a decorator | `src/observability/audit.py::emit_audit` → `logs/agent_actions.jsonl`; `src/observability/tool_logger.py::log_tool_call` → `logs/tool_calls.jsonl`; `traces/phoenix_spans.parquet` |
| Transparency (Art. 13) | Each decision carries its rationale, the cited policy clause, and the fraud indicators with their evidence. A model card documents the system. | `src/models.py::RoutingDecision`, `src/models.py::CoverageAssessment`; `docs/model-card.md`; e.g. `logs/agent_actions.jsonl#L332` |
| Human oversight (Art. 14) | Suspected fraud, high value, ambiguous coverage and degraded runs are escalated to a human by deterministic code. An escalated claim cannot be auto-approved, which is enforced three times. | `src/agents/router.py::decide_route`; OG-04 in `src/guardrails/output_guard.py::check_auto_approval`; `tests/test_routing.py::test_escalated_and_auto_approved_cannot_even_be_constructed`; `logs/agent_actions.jsonl#L333` |
| Accuracy, robustness, cybersecurity (Art. 15) | Agent-level evaluation over a golden set; timeouts, bounded retries and graceful degradation; input and output guardrails red-teamed | `reports/eval_report.json`; `src/resilience.py::with_resilience`; `tests/test_loops.py`; `reports/redteam_results.json` |

## 2. NIST AI RMF 1.0

| Function | How addressed | Evidence |
|---|---|---|
| GOVERN | This governance pack; the build rules (evidence-in-repo, Gemini-only, synthetic data) enforced by a verifier; named owners per risk | `docs/compliance.md`, `docs/risk-register.md`, `docs/model-card.md`, `docs/output-risk.md`; `scripts/verify_citations.py` |
| MAP | Context and intended/out-of-scope use documented; risks identified and categorised (OWASP LLM Top 10, RMF) | `docs/model-card.md`; `docs/risk-register.md` |
| MEASURE | Golden signals from traces (latency by thinking/acting/tool, tokens, cost, error rate, accuracy, hallucination); agent-level evaluation; failures measured from live spans | `reports/golden_signals.json`, `reports/dashboard_data.csv`, `reports/eval_report.json`, `docs/failure-analysis.md` |
| MANAGE | Guardrails wired into the graph's first and last nodes; escalation to a human; each failure's fix verified by a regression test | `src/guardrails/input_guard.py::screen_input`, `src/guardrails/output_guard.py::screen_output`, `src/graph.py`; `tests/test_failure_regressions.py` |

## 3. DPDP Act 2023 (India)

The deployment context is Indian (₹ amounts, DPDP), so the Digital Personal Data Protection Act is
the relevant data-protection regime. All data here is synthetic. The controls are built so that they
would hold with real data.

| Obligation | How addressed | Evidence |
|---|---|---|
| Purpose limitation (s.4, s.5) | Claimant data is used only to triage the claim it was submitted with. Servicing and other requests are refused by IG-03. Cross-claimant requests are refused by IG-02. | `src/guardrails/input_guard.py::check_out_of_scope`, `src/guardrails/input_guard.py::check_cross_claimant`; `reports/redteam_results.json` (RT-05…RT-07) |
| Data minimisation | Each agent receives only the fields its task needs (context isolation). Identifiers are masked before any write, and the router never sees free text. | `src/context/strategies.py::isolate`; `src/security/masking.py::mask_record`; `tests/test_failure_regressions.py::test_router_still_never_sees_the_narrative` |
| Accuracy of data (s.8(3)) | Structured, validated output at every node; claim-type labels validated in code | `src/models.py`; `src/agents/classifier.py::normalise_claim_type` |
| Storage limitation (s.8(7)) | Long-term memory keeps short masked summaries per claimant, not raw narratives. Checkpoints, memory and the vector index live under a git-ignored `var/` directory that can be deleted as a whole. | `src/memory/long_term.py::remember`; `.gitignore` |
| Right to erasure (s.12) | Erasure hook deletes a claimant's memories, one or all, by masked namespace | `src/memory/long_term.py::forget` |
| Security safeguards (s.8(5)) | Masked identifiers in logs, traces, memory and MCP responses; Presidio PII redaction on input and output; no secrets in the repository | `src/observability/tracing.py::MaskingSpanExporter`; `src/guardrails/pii.py::redact`; verifier check 5 in `scripts/verify_citations.py`; `tests/test_memory_persistence.py::test_memory_never_stores_unmasked_identifiers` |
| Breach detection | Every guard violation is written to the audit trail with its rule id, so a leak attempt is visible after the fact | `logs/agent_actions.jsonl` (actions `guardrail_blocked`, `guardrail_sanitized`); e.g. `logs/agent_actions.jsonl#L421` |
| Notice and consent (s.5, s.6) | **Documented approach, not implemented:** in production, the FNOL intake form would carry the notice and capture consent before submission. The copilot runs only after intake, so it never collects data itself. | this document |

## 4. Provider rule (R4) and the lockfile

Gemini is the only model provider. The verifier (`scripts/verify_citations.py`, check 6) enforces
this in three places: no other-provider import in `src/`, `mcp_server/`, `scripts/` or `tests/`; no
other-provider package among the direct dependencies in `requirements.txt`; and no other-provider key
in `.env.example`. `deepeval` and `llm-guard` do install `openai`, `anthropic`, `langchain-openai`
and `langchain-anthropic` into the environment as **transitive** dependencies. They are never
imported or configured. The DeepEval judge is a Gemini wrapper (`scripts/run_eval.py::build_judge`),
and `src/config.py` refuses to start if another provider's key is present in the environment. A
`pip freeze` listing those packages is therefore not a breach of Rule R4.

## 5. Gaps

- **Notice/consent** is a documented approach only. The CLI has no intake form.
- **Erasure** is implemented for long-term memory. Checkpoints are erased by deleting `var/`; there is
  no per-thread purge command.
- **Accuracy** is measured on 12 synthetic claims (`reports/eval_report.json`). That is not enough for
  a conformity claim.
