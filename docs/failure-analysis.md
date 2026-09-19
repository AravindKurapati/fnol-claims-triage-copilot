# Failure-Mode Analysis — FNOL Claims-Triage Copilot

**AC-08 · SPEC-07 §2.4.** Eight real failures, found by running the copilot live against Gemini under
Arize Phoenix tracing (`python -m src.main run` / `python -m src.main trace`). None was staged. Every
`run_id` and `span_id` below resolves in the committed trace export `traces/phoenix_spans.parquet`
(columns `run_id`, `context.span_id`). Tool-log citations point at lines of `logs/tool_calls.jsonl`.
`scripts/verify_citations.py` checks every one of them (Rule R2).

The export is cumulative (`scripts/export_traces.py`), so the parquet holds the failing runs *and*
the runs that verify each fix:

| run_id | What it is | Claims | Outcome |
|---|---|---|---|
| `run-4d452c0b72d6` | first live run, claim 001 | 1 | crashed: F-01 |
| `run-042589191502` | after the F-01 fix, claim 001 | 1 | degraded: F-02 |
| `run-4664e203f3ff` | first full traced batch | 12 | 8 claims degraded: F-03 |
| `run-61e03b1188df` | batch after F-01…F-03 fixes | 12 | 10/12 route match; F-04, F-05 found |
| `run-7080f359ae3c` | batch after F-04, F-05 fixes | 12 | F-01…F-05 verified; F-06 and F-07 found in its spans and audit log |
| `run-28184ad47492` | final Phase 6 batch, after F-06…F-08 fixes | 12 | the evaluated run (`reports/eval_report.json`); F-07 verified |

---

### F-01 — Every live run crashed: the SQLite checkpointer does not support async

- **Observed:** run_id `run-4d452c0b72d6`, span_id `c6ed6802835677a5` (`LangGraph`, status `ERROR`)
  under root span_id `fb77ec54955c0cac` (`triage_claim`, status `ERROR`, 36 ms).
- **Symptom:** the very first live claim produced no triage. The span's status message is
  `NotImplementedError: The SqliteSaver does not support async methods. Consider using
  AsyncSqliteSaver instead.` `run_claim` caught it and returned the degraded fallback, so the CLI
  printed `DEGRADED` and "Automated triage could not complete". Every claim would have failed the
  same way.
- **Root cause:** `src/memory/short_term.py::build_checkpointer` built a synchronous `SqliteSaver`,
  but the graph is invoked with `ainvoke` (async by design, NFR-04). Phases 1–3 compiled the graph
  and unit-tested the checkpointer, but never invoked the compiled graph end to end, so the mismatch
  never ran.
- **Fix:** `build_checkpointer` returns an `AsyncSqliteSaver` over the same file when an event loop
  is running (the sync saver stays only for compile-only callers such as `src.main graph`). A
  follow-on defect surfaced by the fix: aiosqlite's worker thread is non-daemon, so the CLI process
  hung after finishing. That is fixed by `src/graph.py::TriageGraph.aclose`, called from `src/main.py`.
- **Verified by:** `run-042589191502` gets past the checkpointer and reaches the supervisor (next
  entry), and
  `tests/test_memory_persistence.py::test_checkpointer_supports_async_graph_invocation` reproduces
  the failure against the old saver and passes against the new one.

### F-02 — Retired model id: every Gemini call 404'd, then the claim was bounced back to the claimant

- **Observed:** run_id `run-042589191502`, claim CLM-2026-000001. Three LLM span_ids,
  `9b0d9213f1f7d2e3`, `b77169807f303db1` and `fc93f7ad0f32c330` (all `ChatGoogleGenerativeAI`,
  status `ERROR`), under supervisor span_id `f7908b332bd17c2f`. The run then went to clarify
  span_id `8a82648c5adb8d80`.
- **Symptom:** a clean ₹18,000 windscreen claim (fixture: fast-track) was answered with "We could not
  determine what is being reported. Please describe the loss…". Meanwhile the CLI had printed
  `Gemini ready (gemini-2.5-flash)`.
- **Root cause:** four compounding defects:
  1. `gemini-2.5-flash` returns `404 NOT_FOUND … no longer available to new users`;
  2. `src/llm.py::probe` only *constructed* the client, so a dead model id passed the startup check;
  3. `src/resilience.py::with_resilience` retried the permanent 404 like a transient error (3 attempts
     plus backoff, visible as the three ERROR spans);
  4. `src/agents/supervisor.py::classify_intent` maps "model unavailable" to intent `ambiguous`, and
     `decide_next` sent `ambiguous` to `clarify`. That contradicts the module's own stated rule
     ("unknown intent is escalated, never assumed").
- **Fix:** (1) the default model is now a current one, via `src/config.py` and `.env.example`.
  (2) `probe` makes one real minimal call. (3) `src/resilience.py::is_permanent` stops retrying on
  `NOT_FOUND` / auth / invalid-argument errors. (4) The supervisor node marks the run degraded when
  intent is unavailable, and `decide_next` sends a degraded zero-confidence intent to `escalate`.
- **Verified by:** `tests/test_routing.py::test_degraded_intent_escalates_rather_than_asking_the_claimant_to_clarify`
  and `tests/test_loops.py::test_non_retryable_model_error_fails_fast_instead_of_cascading_retries`.
  Live, F-03's run shows the new behaviour: every claim whose intent call failed was escalated
  (e.g. escalate span_id `46c339f0f0ac38e7`), none was asked to clarify.

### F-03 — Free-tier rate limit: 8 of 12 claims degraded because retries ignored the 429's retry delay

- **Observed:** run_id `run-4664e203f3ff` (first full batch). The LLM spans show 26 `ERROR` against
  8 `OK`. For CLM-2026-000002: LLM span_ids `e51929778e4aa404`, `ed570354af2743ca` and
  `fc0f60914e791524` (`ERROR`) under supervisor span_id `b8491a8238eb4486`, then escalate span_id
  `46c339f0f0ac38e7`.
- **Symptom:** batch summary `routing+escalation match: 2/12`. Only the first and last claims got a
  triage. The status message is `429 RESOURCE_EXHAUSTED … Quota exceeded for metric:
  generate_content_free_tier_requests, limit: 5, model: gemini-3.6-flash … Please retry in
  23.3s`.
- **Root cause:** the API key is on the free tier: 5 requests/minute per model. The batch issues
  ~4 model calls per claim back to back. `with_resilience` backed off for 0.5–2 s, but the provider
  asked for ~23 s, so every retry landed inside the same exhausted window. The F-02 fix routed those
  claims safely to a human, but a human queue full of claims that were never triaged is still a
  failure.
- **Fix:** (1) `src/resilience.py::retry_after` parses the 429's `retry in Ns` / `retryDelay` and
  sleeps at least that long (capped at 65 s). (2) `src/llm.py::_Pacer` spaces model calls to
  `GEMINI_RPM` (default 12) per model, so the quota is respected proactively rather than discovered.
  (3) The agents run on `gemini-3.5-flash-lite` and the DeepEval judge on `gemini-3.1-flash-lite`,
  because free-tier quotas are per model; the price basis in `src/config.py` was updated to match.
- **Verified by:** `tests/test_loops.py::test_rate_limit_waits_for_the_providers_retry_delay`. Live:
  `run-61e03b1188df` and `run-7080f359ae3c` complete all 12 claims with no degraded claim.

### F-04 — Racing exclusion missed: the coverage agent never saw how the loss happened

- **Observed:** run_id `run-61e03b1188df`, claim CLM-2026-000008 (fixture: not covered, racing
  exclusion §6.1). The coverage node is span_id `16e81cb6e7134ddd`. Its LLM span_id
  `1999a57eed2b79fc` output `"status": "covered", "cited_clause": "AUTO-COMP-2026 §4.2" … "No
  exclusions apply."` The exclusion-sweep tool call is span_id `bac9a6248fa2370f`, logged at
  `logs/tool_calls.jsonl#L77`.
- **Symptom:** a crash on "the third timed lap" of a "track day event" was assessed as covered
  under the collision clause. The route still matched the oracle (both `standard`), so only
  clause-level evaluation would catch it. In production this is a wrongful payout.
- **Root cause:** the tool log shows retrieval was *not* at fault: line 77's exclusion sweep
  returned §6.1 "Racing and Speed Testing" at rank 1. The model simply had no facts to test it
  against. Context isolation in `src/context/strategies.py` gave the coverage agent `claim_facts,
  classification, retrieved, policy, coverage_rules` but **not the narrative**. "Track day" and
  "timed lap" exist only in the claimant's narrative, and the classifier's evidence quotes ("hit the
  barrier") did not carry them. So no exclusion that depends on *circumstances* (racing, unlicensed
  driver, wear and tear) could ever fire.
- **Fix:** the coverage view now includes the narrative, and it still reaches the prompt only
  fenced, through `src/context/quarantine.py::render_for_prompt` (NFR-03 intact). The router's view
  is unchanged. `COVERAGE_SYSTEM` in `src/agents/coverage.py` tells the agent to test each retrieved
  exclusion against the narrative's circumstances. The exclusion sweep query is now seeded with the
  classifier's evidence phrases (masked).
- **Verified by:** run_id `run-7080f359ae3c`, same claim. Coverage LLM span_id `681e68b92dac271b`
  output `"status": "not_covered", "cited_clause": "AUTO-COMP-2026 §6.1"`, "…used in a timed lap
  event at a track"; tool call at `logs/tool_calls.jsonl#L133`. Regression tests:
  `tests/test_failure_regressions.py::test_coverage_context_carries_the_fenced_narrative` and
  `tests/test_failure_regressions.py::test_router_still_never_sees_the_narrative`.

### F-05 — Classifier label drift silently disabled two fraud indicators

- **Observed:** run_id `run-61e03b1188df`, claim CLM-2026-000007. Classifier LLM span_id
  `6f737076889d963c` returned `"claim_type": "property: burglary"`; fraud node span_id
  `e18ccbc015ee034e` then produced risk `low` from only FI-02 and FI-03 (score 0.22). Across the run,
  labels came back as `auto: collision`, `property: fire`, `auto: theft`.
- **Symptom:** a burglary with no police report and a prior burglary inside 12 months (fixture:
  medium fraud risk) was scored low risk. The same run's tool log shows the malformed label leaking
  into RAG queries ("exclusions that defeat cover for this loss: auto: collision",
  `logs/tool_calls.jsonl#L77`).
- **Root cause:** `CLASSIFIER_SYSTEM` in `src/agents/classifier.py` listed the vocabulary as
  `auto: collision, theft, …`, and the model copied the layout into the label. `claim_type` was an
  unvalidated `str`. `src/agents/fraud_indicators.py` matches loss types exactly, so FI-05 (prior
  same-type claim) and FI-07 (theft/burglary without a police report) never matched
  `"property: burglary"`. Evaluation would also have scored every such label as wrong.
- **Fix:** the prompt now asks for one bare label per line of business, and
  `src/agents/classifier.py::normalise_claim_type` validates the label in code. It strips a
  line-of-business prefix, snake-cases the label, and maps anything outside that line's vocabulary
  (`CLAIM_TYPES`) to `unknown`.
- **Verified by:** run_id `run-7080f359ae3c`. Classifier LLM span_id `a6f9b11a99a316f5` returns
  `"burglary"`; fraud span_id `84b8f660246ad209` now fires FI-02, FI-03, FI-05 and FI-07 → risk
  `medium` (score 0.56), matching the fixture. Regression tests in
  `tests/test_failure_regressions.py` (`test_claim_type_labels_are_normalised_to_the_vocabulary`
  and two more).

### F-06 — Plaintext policy numbers and claimant ids in every traced run (NFR-05 breach)

- **Observed:** all runs, e.g. run_id `run-7080f359ae3c`, input_guard span_id `8f2b9f06c8b3993f`.
  Its `attributes.input.value` held the full graph state, including `raw_input.policy_number` and
  `claimant.claimant_id` in plaintext. It was one of 630 spans with a plaintext identifier in
  `attributes.input.value` and 70 in `attributes.output.value`, across the `supervisor`,
  `input_guard`, `output_guard`, `ingest` and `LangGraph` spans.
- **Symptom:** `scripts/verify_citations.py` check 5 failed the build: `plaintext policy number /
  claimant id in traces/phoenix_spans.parquet`. The masking chokepoints in
  `src/security/masking.py` covered the tool log, the audit trail, MCP responses and the CLI, but
  not the tracer.
- **Root cause:** `openinference-instrumentation-langchain` serialises each node's full input and
  output state into span attributes. Those attributes are exported to Phoenix, and from there into
  the committed trace. Tracing was a new write path that bypassed every existing chokepoint. A
  second defect surfaced during the fix: the first export-side masking pass also starred out a
  *span id*. `c6ed6802835677a5` contains the 10-digit run `6802835677`, which the phone regex
  matched, and that broke the F-01 citation.
- **Fix:** (1) `src/observability/tracing.py::MaskingSpanExporter` wraps the Phoenix OTLP exporter
  and masks every string attribute before a span leaves the process, so identifiers never reach
  Phoenix. (2) `scripts/export_traces.py` masks attribute values again on export, covering spans
  recorded before the fix, and exempts identity columns (`ID_COLUMNS`: span/trace/parent/run ids),
  which are what the documents cite.
- **Verified by:** the re-exported parquet shows `"policy_number": "POL-AU-****276"` in span
  `8f2b9f06c8b3993f`. `scripts/verify_citations.py` check 5 passes, and check 2 resolves every span
  id in this document. Regression tests:
  `tests/test_failure_regressions.py::test_span_exporter_masks_identifiers_before_they_leave_the_process`,
  `tests/test_failure_regressions.py::test_trace_export_masks_spans_recorded_before_the_fix` and
  `tests/test_failure_regressions.py::test_trace_export_never_masks_the_span_ids_it_is_cited_by`.

### F-07 — The output PII guard redacted the word "Queue" in every escalation instruction

- **Observed:** run_id `run-7080f359ae3c`, claims CLM-2026-000005 and CLM-2026-000006. The output
  guard's audit records at `logs/agent_actions.jsonl#L334`, `logs/agent_actions.jsonl#L335`,
  `logs/agent_actions.jsonl#L344` and `logs/agent_actions.jsonl#L345` (action
  `guardrail_sanitized`, rule `OG-02`, detail `free-text PII redacted (presidio): PERSON`).
- **Symptom:** the handler-facing escalation instruction for a ₹820,000 claim came out as
  "Escalate to a human claims handler — high-value claim (820,000 >= 500,000). `<PERSON>`: standard.
  This is a recommendation, not an approval." The queue label, which is the one thing the handler
  needs, was replaced by a redaction token. Every escalated claim in the run was affected. The route
  itself was still correct in state, so route-level evaluation did not catch it. Only the audit trail
  showed it.
- **Root cause:** `OG-02` ran Presidio over the whole terminal message. That message is a system
  template filled from structured values, with no claimant free text in it. The small spaCy NER model
  behind `src/guardrails/pii.py` tags a capitalised word at the start of a clause ("Queue:") as a
  PERSON. So the guard was scanning text it did not need to scan, and the scan had false positives.
- **Fix:** `src/guardrails/output_guard.py::scrub_model_text` scopes `OG-02` to the model-written
  fields that can actually quote the claimant's narrative (classification evidence, coverage
  rationale, fraud-indicator evidence) and writes the scrubbed copies back as field updates. The
  templated terminal message is still checked by `OG-01` (masked identifiers) and `OG-05`
  (cross-claimant data), but is no longer run through NER.
- **Verified by:** `tests/test_failure_regressions.py::test_output_pii_guard_leaves_system_templates_intact`
  (the exact message above passes through unchanged) and
  `tests/test_failure_regressions.py::test_output_pii_guard_scrubs_model_text_that_quotes_the_narrative`
  (a third party's name and phone number in classifier evidence are still redacted). The post-fix
  red-team run (`scripts/redteam.py`) records `OG-02` firing on the classification field, not on the
  message: `logs/agent_actions.jsonl#L444`. Live, in the final run `run-28184ad47492`, the same
  ₹820,000 escalation of claim 005 passes the output guard untouched (`allow`, no violations):
  `logs/agent_actions.jsonl#L552`.

### F-08 — Tracing silently switched off: the "traced" run would have exported nothing

- **Observed:** the first attempt at the final Phase 6 `python -m src.main trace` run. Its console
  output showed `Phoenix up at http://localhost:6006`, followed by `Phoenix tracing unavailable,
  continuing untraced: TracerProvider.__init__() got an unexpected keyword argument
  'project_name'`, and then the batch carried on. **There is no span id to cite, and that is the
  failure:** the run recorded no spans. We stopped it after the first claim. Its decisions are not in
  the evaluation.
- **Symptom:** `trace` exists to produce the trace evidence. Left alone, it would have finished
  with exit code 0 and re-exported the old spans, and nothing would have flagged the new run as
  untraced.
- **Root cause:** two defects together. (1) `arize-phoenix-otel` 0.17 changed `TracerProvider`: it
  no longer takes `project_name` and passes unknown keyword arguments to the OpenTelemetry SDK, which
  rejects them. `src/observability/tracing.py::init_tracing` still passed `project_name`. (2)
  `init_tracing` deliberately degrades to untraced so that tracing can never take a triage run down
  (NFR-04). That is right for `run` and `batch`, but wrong for `trace`, whose only job is to trace.
  While checking the fix we also confirmed a latent leak: when given an endpoint, phoenix installs a
  default span processor that exports **unmasked** spans alongside our masking one.
- **Fix:** (1) The project name now travels on the OpenTelemetry resource
  (`ResourceAttributes.PROJECT_NAME`). The masking processor is added with
  `replace_default_processor=True`, so it is the only exporter (NFR-05, cf. F-06). (2)
  `src/main.py::cmd_trace` initialises tracing before the batch and exits `1` without running it if
  tracing is not enabled.
- **Verified by:** `tests/test_failure_regressions.py::test_tracing_initialises_against_the_installed_phoenix_otel`
  and `tests/test_failure_regressions.py::test_trace_command_fails_instead_of_exporting_an_untraced_run`.
  Both reproduced the failure before the fix and pass after it. The final traced run (table above)
  was recorded with the fix in place.

---

## Open finding (not a code defect) — O-01: routing rule vs. fixture oracle on claim 007

After F-05, claim 007 correctly fires 4 indicators at medium risk. `src/agents/router.py::decide_route`
then sends it to `investigate`, because SPEC-02 §2.5 says `len(indicators) >= INVESTIGATE_MIN_INDICATORS`
(3) → investigate. The generator's oracle (`scripts/generate_data.py`, scenario
`fraud_medium_standard`) expects `standard`. The router is implementing its specification. The oracle
was written expecting fewer indicators, because the indicators that F-05 had disabled were never
observed firing before. We have **not** changed either side to force a match: SIU referral for a
burglary with no police report, a prior burglary and a 10-day-old policy is defensible. The
disagreement is reported as-is in `reports/eval_report.json` (routing accuracy) pending a product
decision on the threshold (`INVESTIGATE_MIN_INDICATORS` in `.env.example`).

## Patterns

- **Four of the eight failures were integration faults, invisible without running the system
  live.** F-01, F-02 and F-03 (runtime, provider, quota) were each diagnosed straight from a span's
  status message. That is the argument for tracing *before* evaluation. F-08 (a library upgrade) is
  the mirror case: the tracing itself failed, and a degrade-gracefully default hid it. Graceful
  degradation is right for the product path, but wrong for the evidence path.
- **Three failures (F-04, F-05, F-07) passed the route-level oracle.** Only span- or audit-level
  inspection (the coverage LLM output, the classifier label, the fraud indicator list, the output
  guard's audit records) exposed them. This is why the
  evaluation (`reports/eval_report.json`) scores clause, type and fraud-risk accuracy, not just the
  queue.
- **The safety net held.** Across the degraded runs no claim was auto-approved. Every failure
  degraded to a human, which is AC-03's hard rule doing its job (`logs/agent_actions.jsonl`, action
  `escalated`).
