# SPEC-09 — Guardrails, PII, Audit Trail & Secrets Hygiene

**Phase 5 · teammate** · Satisfies: §7.4 all three rows · AC-06, AC-10 · NFR-01, NFR-05.

---

## 1. Purpose & graded requirement

Input/output guardrails **wired into the agent's I/O path** (blocking/sanitizing, not sitting in a module
unused), a **machine-generated audit trail** of consequential actions, and clean secrets hygiene.

## 2. Contract

### 2.1 Guardrails — `src/guardrails/`

The `input_guard` and `output_guard` **nodes already exist** as the first and last nodes of the graph
(SPEC-02 §2.2, P2 shim). Fill the validators; **do not change the topology**.

```python
class GuardVerdict(BaseModel):
    action: Literal["allow","sanitize","block"]
    violations: list[Violation]      # {rule_id, severity, detail}
    sanitized: str | None
```

**Input guard** (Guardrails-AI / LLM Guard validators, or explicit policy functions):

| Rule | Action |
|---|---|
| `IG-01` prompt injection / instruction override | sanitize → quarantine, annotate; never obey |
| `IG-02` cross-claimant data request | **block** + escalate, disclose nothing (AC-06) |
| `IG-03` out-of-scope request | block → clarify (AC-04) |
| `IG-04` PII in the wrong field | sanitize via Presidio |
| `IG-05` oversized / malformed payload | block |

**Output guard:**

| Rule | Action |
|---|---|
| `OG-01` unmasked policy number / claimant id in the answer | sanitize (mask) — AC-06 |
| `OG-02` free-text PII (name, phone, email, address) | sanitize via Presidio |
| `OG-03` coverage claim citing a clause not in `state.retrieved` | block → ambiguous → escalate |
| `OG-04` `auto_approved == True` while `escalation_required == True` | **block** — AC-03 last line of defence |
| `OG-05` refusal leakage (confirming another claimant exists) | block |

Each violation is written to the audit trail with its `rule_id`, which is what `docs/risk-register.md`
(SPEC-10) cites as the committed control.

### 2.2 PII — `src/guardrails/pii.py`
**Presidio** analyzer + anonymizer over free text, layered on top of the deterministic identifier masking
already applied at the logging chokepoints (`src/security/masking.py`, P1). Bonus artifact: a before/after
redaction sample.

### 2.3 Audit trail — `src/observability/audit.py` → `logs/agent_actions.jsonl` (AC-10)

`emit_audit()` **call sites already exist** throughout the graph (P2 shim). Fill the writer:

```jsonc
{"timestamp":"2026-09-18T11:02:04.001Z","run_id":"run-9f2c...","actor":"router",
 "action":"routing_decided","tool":null,
 "decision":{"queue":"investigate","escalation_required":true,"auto_approved":false},
 "rationale":"fraud.risk=high (FI-02, FI-06, FI-09)","claim_id":"CLM-2026-000117"}
```

Consequential actions that **must** appear: `input_screened`, `intent_identified`, `coverage_decided`,
`fraud_assessed`, `routing_decided`, `escalated` | `finalized` | `clarification_requested`,
`output_screened`, plus every guard `block`. Append-only JSONL; masked (NFR-05).

### 2.4 Secrets hygiene — NFR-01
`.env.example` with every variable and no real values · `.gitignore` covering `.env`, `var/`, `*.sqlite*`,
`.chroma/` · no key in any committed file, log, notebook or trace. Verify with `git grep -nE
'AIza|api[_-]?key\s*=\s*["\x27][^"\x27]{10,}'`.

## 3. Done when

- [ ] A red-team input is **blocked** end-to-end, and the block appears in `logs/agent_actions.jsonl`.
- [ ] `OG-04` demonstrably fires on a forced bad decision (AC-03).
- [ ] Guardrails are invoked from the graph's I/O nodes — provable from the trace, not just the code.
- [ ] `logs/agent_actions.jsonl` is machine-generated and covers every action in §2.3.
- [ ] `.env.example` + `.gitignore` committed; secret scan clean; no plaintext identifier in any log.
