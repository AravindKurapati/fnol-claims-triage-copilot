# SPEC-05 — Custom MCP Server & Client

**Phase 3** · Satisfies: §7.1 "MCP server" row — `mcp_server/` + `logs/mcp_transcript.jsonl`,
**≥ 2 tools + 1 resource**, consumed via **langchain-mcp-adapters**, with a committed tool-call transcript.

---

## 1. Purpose & graded requirement

A real MCP server the agent talks to over **stdio** using the MCP Python SDK, adapted into LangChain tools
by `langchain-mcp-adapters`, with a machine-generated transcript proving the calls happened.

## 2. Contract

### 2.1 Server — `mcp_server/server.py`

MCP Python SDK, **stdio** transport. Reads only committed synthetic fixtures from `data/policy_corpus/`.

**Tools (3 — one more than the minimum):**

| Tool | Input | Output | Used by |
|---|---|---|---|
| `lookup_policy` | `{policy_number: str}` | `{found, product, status, inception_date, expiry_date, sum_insured, deductible, currency, coverages[], exclusions[]}` — **identifiers masked in the response** | `coverage` |
| `check_claim_history` | `{claimant_id: str, months: int = 24}` | `{claim_count, claims:[{claim_id_masked, loss_date, amount, outcome, loss_type}], same_type_within_12m: bool}` | `fraud` |
| `validate_coverage_window` | `{policy_number, loss_date, reported_at}` | `{policy_active_on_loss_date, days_to_report, within_reporting_deadline, deadline_days}` | `coverage`, `fraud` (FI-01, FI-02) |

**Resource (1):**

| URI | Content |
|---|---|
| `policy://handbook/coverage-rules` | The underwriting coverage-rules handbook: severity bands, deductible application order, reporting deadlines per product, exclusion precedence. Markdown, served from `data/policy_corpus/coverage_rules.md`. |

**Server-side rules:**
- Unknown `policy_number` / `claimant_id` returns `{"found": false}` — **never** an error that discloses
  existence, and never a near-match suggestion (AC-06).
- Every response is masked **before** it leaves the server; the agent never receives a plaintext identifier.
- Inputs are validated against a JSON schema; a malformed input returns a typed error object, which is the
  error path `tests/test_tool_contracts.py` (P6) asserts.

### 2.2 Client — `src/mcp_client.py`

```python
async def load_mcp_tools() -> list[BaseTool]       # via langchain_mcp_adapters MultiServerMCPClient
async def read_coverage_rules() -> str             # reads the MCP *resource*, not a tool
```

- Launches the server as a stdio subprocess (`python -m mcp_server.server`); no network, no Docker.
- Every adapted tool is re-registered through `src/tools/registry.py` so it wears `@logged_tool` — this is
  what makes AC-07's "tool names reconcile with the agent/MCP code" true by construction.
- Connection failure is **degradation, not a crash** (NFR-04): returns `[]`, sets `state.degraded`.

### 2.3 Transcript — `logs/mcp_transcript.jsonl`

One JSON object per MCP interaction, appended **by the client**:

```jsonc
{"ts":"2026-09-18T11:02:03.118Z","direction":"call","session":"mcp-4f1c","kind":"tool",
 "name":"lookup_policy","args":{"policy_number":"POL-AU-****209"},"run_id":"run-9f2c1ab77d04"}
{"ts":"2026-09-18T11:02:03.204Z","direction":"result","session":"mcp-4f1c","kind":"tool",
 "name":"lookup_policy","status":"ok","latency_ms":86,"result_summary":{"found":true,"product":"AUTO-COMP-2026"}}
{"ts":"2026-09-18T11:02:03.310Z","direction":"call","session":"mcp-4f1c","kind":"resource",
 "name":"policy://handbook/coverage-rules","run_id":"run-9f2c1ab77d04"}
```

Must contain at least one `kind:"resource"` record — the resource is graded separately from the tools.
Args and results pass through `mask_record()` before write (NFR-05).

## 3. Behaviour

- The server is **stateless**; fixtures are loaded once at startup.
- Tool results are returned as JSON strings; the client parses and validates against the declared output
  schema before handing to the agent — a schema mismatch is a tool-contract failure, not silent data.
- Server lifetime is the CLI process lifetime; the client shuts the subprocess down cleanly on exit so the
  transcript is flushed.

## 4. Evidence produced

| Artifact | Producer |
|---|---|
| `mcp_server/server.py` + fixtures | hand-written |
| `src/mcp_client.py` | hand-written |
| `logs/mcp_transcript.jsonl` | `src/mcp_client.py` on every run |

## 5. Done when

- [ ] `python -m mcp_server.server` starts and responds to an MCP handshake over stdio.
- [ ] `load_mcp_tools()` returns 3 tools; `read_coverage_rules()` returns the resource body.
- [ ] A full CLI run appends ≥ 4 call/result pairs **and** ≥ 1 resource read to the transcript.
- [ ] Tool names in the transcript match `registry.list_tools()` exactly.
- [ ] Unknown policy number returns `{"found": false}` with no disclosure.
- [ ] Killing the server mid-run degrades the run instead of crashing it.
- [ ] No plaintext identifier appears anywhere in the transcript.
