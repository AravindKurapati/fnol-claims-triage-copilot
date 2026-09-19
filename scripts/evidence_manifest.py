"""Evidence → producer map (SPEC-12 §2.4 check 3, NFR-06).

Every graded evidence file is listed with the committed code that writes it. The verifier asserts
both halves exist, so an artifact with no producer — "a metric, trace or log a team could
hand-write" (Rule R1) — fails the build instead of being discounted by the grader.
"""

from __future__ import annotations

EVIDENCE: dict[str, list[str]] = {
    # §7.1 foundation
    "logs/mcp_transcript.jsonl": ["src/mcp_client.py"],
    "logs/memory_test.log": ["tests/test_memory_persistence.py"],
    "logs/quarantine_events.jsonl": ["src/context/quarantine.py"],
    # §7.2 observability
    "traces/phoenix_spans.parquet": ["scripts/export_traces.py", "src/observability/tracing.py"],
    "traces/trace_manifest.json": ["scripts/export_traces.py"],
    "logs/tool_calls.jsonl": ["src/observability/tool_logger.py", "src/tools/registry.py"],
    # §7.3 cost governance
    "reports/golden_signals.json": ["scripts/golden_signals.py"],
    "reports/dashboard_data.csv": ["scripts/build_dashboard.py"],
    "reports/dashboard.png": ["scripts/build_dashboard.py"],
    # §7.4 security
    "logs/agent_actions.jsonl": ["src/observability/audit.py"],
    # §7.6 evaluation
    "data/golden_set.json": ["scripts/build_golden_set.py"],
    "reports/eval_report.json": ["scripts/run_eval.py"],
}

# Optional / bonus evidence — checked only if present.
OPTIONAL: dict[str, list[str]] = {
    "reports/redteam_results.json": ["scripts/redteam.py"],
    "reports/pii_redaction_sample.json": ["scripts/redteam.py"],
    "reports/dashboard_chart.png": ["scripts/build_dashboard.py"],
}
