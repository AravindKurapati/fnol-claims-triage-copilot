# Optimisation Note — one MCP session per run

**Measured before and after from two Phoenix-derived golden-signal reports**, both produced by
`scripts/golden_signals.py` from `traces/phoenix_spans.parquet`:

| | Report | Run | Code |
|---|---|---|---|
| **Before** | `reports/golden_signals_baseline.json` | `run-7080f359ae3c` | a new MCP client session for every tool call |
| **After** | `reports/golden_signals.json` | `run-28184ad47492` | one MCP stdio session for the whole run |

Same 12 committed sample claims, same model (`gemini-3.5-flash-lite`), same pacing (`GEMINI_RPM=12`).

## Finding

The baseline trace showed every MCP tool span taking 1.6–5.0 s. The actual work is a dictionary
lookup over `data/policy_corpus/policies.json` or `claim_history.json` (`mcp_server/server.py`).
In contrast, the in-process RAG tool `retrieve_policy_clauses` ran at a p50 of 36 ms, even though it
embeds a query and searches Chroma. So the cost was in the transport, not the tool.
`langchain-mcp-adapters`' `client.get_tools()` returns tools that open a **new stdio session, and so
spawn a new `mcp_server` Python process**, on every invocation. Each call paid the full
interpreter-startup and import cost.

## Change

`src/mcp_client.py::MCPToolset` opens one session with `client.session("fnol-policy")` when the run
starts and loads the tools from that session (`load_mcp_tools(session, …)`). It closes the session
when the run ends. Degradation is unchanged: if the session cannot open, the tools return empty
results and the run continues (`tests/test_tool_contracts.py::test_mcp_server_down_degrades_to_empty_not_a_crash`).

## Result (latency in ms, from the two reports)

| Span | Before p50 | After p50 | Before p95 | After p95 |
|---|---:|---:|---:|---:|
| `check_claim_history` (MCP) | 1,730.3 | **10.5** | 2,553.4 | **15.3** |
| `lookup_policy` (MCP) | 1,782.4 | **10.1** | 2,742.3 | **15.0** |
| `validate_coverage_window` (MCP) | 1,763.3 | **12.4** | 3,980.8 | **16.7** |
| all tool spans (n = 47) | 1,629.2 | **15.5** | 2,808.1 | **45.1** |
| `retrieve_policy_clauses` (in-process control) | 35.9 | 30.3 | 180.4 | 127.4 |
| end-to-end per claim (n = 12) | 19,902.4 | 19,923.3 | 42,871.5 | 28,414.7 |

- **MCP tool latency fell about 170×** at the median (≈ 1.75 s → ≈ 11 ms per call). The in-process RAG
  tool, which did not change, stayed flat. That is the control showing the gain comes from the
  change and not from a quieter machine.
- **End-to-end p50 did not move.** That is expected, and it is the honest headline: a claim's wall
  time is dominated by the four paced Gemini calls (60 s / 12 RPM = 5 s spacing, visible as the
  ~5 s `classifier`, `coverage` and `fraud` node spans). The ~5 s of MCP time saved per claim is
  absorbed by waiting for the next pacing slot. The p95 and max did fall (42.9 s → 28.4 s,
  68.6 s → 32.5 s). Part of that is the removed tool time, and part is fewer rate-limit waits, so we
  do not attribute the whole tail improvement to this change.
- **Tokens and cost are unaffected** (35,446 vs 37,082 tokens; $0.0163 vs $0.0171 per 12-claim
  batch), as expected: MCP calls use no model tokens.

## What this means

On a paid tier without pacing, the saving (~5 s per claim across three MCP calls) would show up
directly in end-to-end latency. On the free tier, the binding constraint is the model rate limit.
The next optimisation would therefore be to reduce model calls per claim: the supervisor's intent
call could be skipped when the input guard has already classified the request. That change has
**not** been made or measured.
