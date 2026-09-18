# MCP servers in this project

Two MCP servers are configured in [`.mcp.json`](../.mcp.json) at the repo root. They serve very
different purposes and only one of them is part of the graded system.

> **Filename note:** Claude Code loads project-scoped MCP config from **`.mcp.json`** (leading dot).
> A file named `mcp.json` is not read by anything. The config is checked in, so every teammate gets
> the same servers on clone; Claude Code asks for approval the first time it sees a project's servers.

---

## 1. `fnol-policy` — the graded custom MCP server

**This is the §7.1 "MCP server" artifact.** Spec: [`specs/SPEC-05-mcp-server.md`](../specs/SPEC-05-mcp-server.md).

| | |
|---|---|
| Implementation | `mcp_server/server.py` (MCP Python SDK, stdio) |
| Tools | `lookup_policy`, `check_claim_history`, `validate_coverage_window` (3 — one above the ≥2 minimum) |
| Resource | `policy://handbook/coverage-rules` (1 — the minimum) |
| Consumed by the agent via | `src/mcp_client.py` using **langchain-mcp-adapters** |
| Evidence | `logs/mcp_transcript.jsonl` |

**The graded consumption path is `src/mcp_client.py`, not this config file.** The rubric scores the
agent talking to the server through langchain-mcp-adapters at runtime; the `.mcp.json` entry only
lets *us* exercise the same server by hand from Claude Code while developing. Both paths launch the
identical `python -m mcp_server.server` subprocess, so what you poke at interactively is exactly
what the graph calls.

Try it by hand:

```bash
.venv/bin/python -m mcp_server.server     # stdio; expects an MCP handshake on stdin
```

---

## 2. `playwright` — development tooling only

**Not part of the graded system.** It is a Claude Code convenience, deliberately kept outside the
project's runtime:

- **not** in `requirements.txt`;
- **not** imported anywhere under `src/`, `mcp_server/`, `scripts/` or `tests/`;
- runs via `npx`, so it adds nothing to the Python environment;
- `--isolated` keeps browser profile state out of the repo.

### Why it is here at all

Phase 5 needs `reports/dashboard.png` — a screenshot of the **Arize Phoenix UI** latency/cost/token
dashboard at `localhost:6006` (AC-09, [`SPEC-08 §2.2`](../specs/SPEC-08-cost-governance.md)). Driving
that capture with Playwright makes the screenshot **reproducible from a script** rather than a
one-off manual grab, which matters under the Evidence-in-Repo rule: evidence a human could have
hand-made is heavily discounted. The CSV half of that artifact is already script-produced by
`scripts/build_dashboard.py`; Playwright lets the PNG half be too.

### Rule R4 note

The Open-Source & Gemini-Only rule fixes the **project's** stack: pip + Python, no Docker, no
external DB service, Gemini as the only model provider. A Node-based editor tool used to capture a
screenshot is not a project dependency and does not touch any of that — the copilot still installs
and runs from `pip install -r requirements.txt` alone. If the Phase 5 owner would rather take the
screenshot manually, delete the `playwright` block and nothing in the system changes.

`scripts/verify_citations.py` (SPEC-12 §2.4) checks Rule R4 against direct dependencies and source
imports, so this entry cannot trip it.
