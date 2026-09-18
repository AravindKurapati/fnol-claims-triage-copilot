# specs/ — Component Specifications

Buildable contracts. One spec per component: schemas, interfaces, file formats, and the acceptance checks
that close it. Architecture rationale lives in [`../design.md`](../design.md); the rules and phase plan live
in [`../CLAUDE.md`](../CLAUDE.md); the project map is [`../AGENTS.md`](../AGENTS.md).

| Spec | Component | Phase | Owner |
|---|---|---|---|
| [SPEC-01](SPEC-01-data-contracts.md) | Data contracts & synthetic data | P1 | this session |
| [SPEC-02](SPEC-02-graph-and-agents.md) | LangGraph graph & agents | P2 | this session |
| [SPEC-03](SPEC-03-context-engineering.md) | Context engineering & quarantine | P3 | this session |
| [SPEC-04](SPEC-04-memory.md) | Tiered memory | P3 | this session |
| [SPEC-05](SPEC-05-mcp-server.md) | MCP server & client | P3 | this session |
| [SPEC-06](SPEC-06-agentic-rag.md) | Agentic RAG | P3 | this session |
| [SPEC-07](SPEC-07-observability.md) | Phoenix observability | P4 | teammate |
| [SPEC-08](SPEC-08-cost-governance.md) | Cost & latency governance | P5 | teammate |
| [SPEC-09](SPEC-09-security-guardrails.md) | Guardrails, PII, audit, secrets | P5 | teammate |
| [SPEC-10](SPEC-10-governance-pack.md) | Governance & compliance docs | P5 | teammate |
| [SPEC-11](SPEC-11-evaluation-and-tests.md) | Evaluation & agent tests | P6 | teammate |
| [SPEC-12](SPEC-12-cli-and-runbook.md) | CLI, runbook, citation verifier | P1 + P6 | both |

## How to read a spec

Every spec has the same five sections:

1. **Purpose & graded requirement** — which AC/NFR/§7 row it satisfies.
2. **Contract** — schemas, signatures, file formats. This is the part other phases depend on; changing it
   is a cross-phase breaking change.
3. **Behaviour** — what it does, including edge and failure cases.
4. **Evidence produced** — the committed artifact(s), and the committed code that produces them (Rule R1).
5. **Done when** — the checks that close the spec.

## Frozen interfaces

These are consumed by Phases 4–6 and **must not change** after P3 closes (see `AGENTS.md` §3):
`TriageState` field names · node names in `src/graph.py` · tool names in `src/tools/registry.py` ·
`emit_audit()` and `logged_tool()` signatures · `run_id` / `thread_id` semantics.
