# SPEC-10 — Governance & Compliance Pack

**Phase 5 · teammate** · Satisfies: §7.5 all four rows · AC-11. **Citation-gated (Rule R2).**

---

## 1. Purpose & graded requirement

Four markdown documents where **every mitigation and every claim cites a committed control** — a file path,
a module, a rule id, a log record, or a Phoenix span. An uncited claim scores as missing; a citation that
does not resolve is treated as missing.

## 2. Contract

### 2.1 `docs/risk-register.md`

| Column | Notes |
|---|---|
| Risk ID | `R-01`… |
| Risk | one sentence |
| Category | **OWASP LLM Top 10** (LLM01 Prompt Injection, LLM02 Insecure Output, LLM06 Sensitive Info Disclosure, …) and/or **NIST AI RMF** function (GOVERN / MAP / MEASURE / MANAGE) |
| Likelihood / Impact | L/M/H |
| Mitigation | **must cite the committed control**, e.g. `src/context/quarantine.py::render_for_prompt`, guard rule `IG-02`, `tests/test_routing.py::test_high_value_escalates` |
| Evidence | the artifact that proves it fired, e.g. `logs/agent_actions.jsonl` record, `docs/failure-analysis.md#F-01` |
| Residual risk | L/M/H after mitigation |
| Owner | role |

Minimum coverage: prompt injection · cross-claimant disclosure · PII leakage in logs · hallucinated clause
citation · wrongful auto-approval · runaway loop / cost blowout · model unavailability · stale policy corpus.

### 2.2 `docs/model-card.md`
Model (**Gemini**, exact id + version from `config`) · data (**synthetic**, pointing at
`scripts/generate_data.py` and `data/`) · intended use (decision **support** for an FNOL handler) ·
limitations · **known failure modes citing `docs/failure-analysis.md`** · out-of-scope uses (no autonomous
settlement, no real PII, no production claims data) · evaluation summary citing `reports/eval_report.json`
and `reports/golden_signals.json`.

### 2.3 `docs/compliance.md`
Three-column mapping, one row per obligation: **obligation → how addressed → evidence artifact (path)**.

| Framework | Obligations to cover |
|---|---|
| **EU AI Act** | risk classification of the system; human oversight (Art. 14) → escalation control; transparency (Art. 13) → decision rationale; record-keeping / logging (Art. 12) → `logs/agent_actions.jsonl`; accuracy & robustness (Art. 15) → eval + resilience |
| **NIST AI RMF** | GOVERN → this pack; MAP → risk register; MEASURE → `reports/golden_signals.json`, `reports/eval_report.json`; MANAGE → guardrails + escalation |
| **DPDP (India)** | purpose limitation · data minimisation → masking · storage limitation · **erasure → `src/memory/long_term.py::forget`** · security safeguards · notice/consent (documented approach) |

### 2.4 `docs/output-risk.md`
Low / medium / high output tiers, how high-risk is gated, and a **worked sample**.

| Tier | Example output | Gate |
|---|---|---|
| Low | claim type + severity classification | none beyond output guard |
| Medium | coverage assessment with clause citation | clause-integrity check `OG-03` |
| **High** | routing decision, escalation, anything resembling approval/denial | **human-in-the-loop** — `escalation_required` forces a human; `auto_approved` is blocked by `OG-04`; refusal path for out-of-scope |

Include one real end-to-end sample (a high-value claim) showing the gate firing, with its audit records.

## 3. Done when

- [ ] All four docs present at the exact paths.
- [ ] Every mitigation/claim cites a committed control; `scripts/verify_citations.py` resolves **all** of them.
- [ ] Model card's failure modes link to real entries in `docs/failure-analysis.md`.
- [ ] Compliance mapping names all three frameworks with real evidence paths.
- [ ] Output-risk doc includes a worked high-risk sample with its `logs/agent_actions.jsonl` records.
