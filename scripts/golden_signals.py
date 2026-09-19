"""Golden-signals report — AC-09, SPEC-08 §2.1 → `reports/golden_signals.json`.

    python scripts/golden_signals.py                 # latest full traced run in the parquet
    python scripts/golden_signals.py --run-id run-…  # a specific run

**Phoenix-derived.** Reads the spans Phoenix recorded (`traces/phoenix_spans.parquet`, written by
`scripts/export_traces.py` from `px.Client().get_spans_dataframe()`) and classifies each span:

    thinking — LLM spans (Gemini reasoning / generation)
    acting   — agent-node spans (the LangGraph nodes: supervisor, classifier, coverage, …)
    tool     — TOOL spans (MCP + RAG tool invocations, one per call)

then emits p50/p95/max latency per type, per node and per tool, token totals from the LLM spans,
cost = tokens × the price basis in `src/config.py` (env-overridable, so the basis is auditable), and
imports accuracy + hallucination rate from `reports/eval_report.json`. Run the eval first (or re-run
this after it) so the quality block is populated.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402

PARQUET = ROOT / "traces/phoenix_spans.parquet"
EVAL = ROOT / "reports/eval_report.json"
OUT = ROOT / "reports/golden_signals.json"

NODE_NAMES = ["input_guard", "ingest", "supervisor", "classifier", "coverage", "fraud", "router",
              "clarify", "escalate", "finalize", "output_guard"]
PROMPT_COLS = ["attributes.llm.token_count.prompt"]
COMPLETION_COLS = ["attributes.llm.token_count.completion"]


def load_spans(run_id: str | None = None) -> tuple[Any, str]:
    import pandas as pd

    if not PARQUET.exists():
        raise SystemExit("traces/phoenix_spans.parquet missing — run `python -m src.main trace`.")
    df = pd.read_parquet(PARQUET)
    if run_id is None:
        roots = df[df["name"] == "triage_claim"]
        if roots.empty:
            raise SystemExit("no triage_claim root spans in the parquet")
        counts = roots.groupby("run_id")["context.trace_id"].nunique()
        latest = roots.groupby("run_id")["start_time"].max()
        full = counts[counts >= counts.max()].index  # the largest (full batch) runs …
        run_id = str(latest[full].sort_values().index[-1])  # … most recent of those
    df = df[df["run_id"] == run_id].copy()
    if df.empty:
        raise SystemExit(f"run {run_id} not in the parquet")
    return df, run_id


def span_type(row: Any) -> str:
    kind = str(row.get("span_kind") or "").upper()
    if kind == "LLM":
        return "thinking"
    if kind == "TOOL":
        return "tool"
    if row["name"] == "triage_claim":
        return "end_to_end"
    if row["name"] in NODE_NAMES:
        return "acting"
    return "other"  # framework plumbing (LangGraph wrapper, RunnableSequence, parsers)


def _num(df: Any, cols: list[str]) -> Any:
    import pandas as pd

    for c in cols:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce").fillna(0)
    return pd.Series([0] * len(df), index=df.index, dtype="float64")


def owning_node(df: Any) -> Any:
    """For every span, the nearest ancestor that is a graph node — attributes LLM tokens to agents."""
    parent = dict(zip(df["context.span_id"], df["parent_id"]))
    name = dict(zip(df["context.span_id"], df["name"]))

    def walk(sid: str) -> str | None:
        seen = 0
        while sid and seen < 50:
            if name.get(sid) in NODE_NAMES:
                return name[sid]
            sid = parent.get(sid)
            seen += 1
        return None

    return df["context.span_id"].map(walk)


def pct(series: Any) -> dict[str, float | None]:
    s = series.dropna()
    if s.empty:
        return {"p50": None, "p95": None, "max": None, "n": 0}
    return {"p50": round(float(s.quantile(0.5)), 1), "p95": round(float(s.quantile(0.95)), 1),
            "max": round(float(s.max()), 1), "n": int(len(s))}


def annotate(df: Any) -> Any:
    """Add span_type / node / tokens / cost columns — shared with scripts/build_dashboard.py."""
    df = df.copy()
    df["span_type"] = df.apply(span_type, axis=1)
    df["node"] = owning_node(df)
    df["tokens_in"] = _num(df, PROMPT_COLS)
    df["tokens_out"] = _num(df, COMPLETION_COLS)
    df.loc[df["span_type"] != "thinking", ["tokens_in", "tokens_out"]] = 0
    df["cost_usd"] = (df["tokens_in"] / 1000 * settings.price_per_1k_input
                      + df["tokens_out"] / 1000 * settings.price_per_1k_output)
    return df


def quality() -> dict[str, Any]:
    if not EVAL.exists():
        return {"accuracy": None, "hallucination_rate": None, "source": None,
                "note": "run `python -m src.main eval` first, then re-run this script"}
    ev = json.loads(EVAL.read_text(encoding="utf-8"))
    m = ev.get("metrics", {})
    return {
        "accuracy": ev.get("accuracy"),
        "hallucination_rate": m.get("hallucination_rate"),
        "faithfulness": m.get("faithfulness"),
        "answer_relevancy": m.get("answer_relevancy"),
        "routing_accuracy": m.get("routing_accuracy"),
        "escalation_recall": m.get("escalation_recall"),
        "citation_validity": m.get("citation_validity"),
        "eval_run_id": ev.get("run_id"),
        "eval_generated_at": ev.get("generated_at"),
        "source": "reports/eval_report.json",
    }


def build(run_id: str | None = None) -> dict[str, Any]:
    df, run_id = load_spans(run_id)
    df = annotate(df)
    lat = df["latency_ms"]
    claims = int((df["span_type"] == "end_to_end").sum())
    tin, tout = int(df["tokens_in"].sum()), int(df["tokens_out"].sum())
    cost = float(df["cost_usd"].sum())

    by_node = {n: pct(lat[(df["span_type"] == "acting") & (df["name"] == n)])
               for n in NODE_NAMES if ((df["span_type"] == "acting") & (df["name"] == n)).any()}
    tools = sorted(df.loc[df["span_type"] == "tool", "name"].unique())
    by_tool = {t: pct(lat[(df["span_type"] == "tool") & (df["name"] == t)]) for t in tools}
    llm = df[df["span_type"] == "thinking"]
    tokens_by_node = {
        str(n): {"input": int(g["tokens_in"].sum()), "output": int(g["tokens_out"].sum()),
                 "llm_calls": int(len(g))}
        for n, g in llm.groupby(llm["node"].fillna("unattributed"))
    }
    errors = int((df.get("status_code", "OK").astype(str) == "ERROR").sum()) \
        if "status_code" in df.columns else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "producer": "scripts/golden_signals.py",
        "source": "traces/phoenix_spans.parquet (Phoenix get_spans_dataframe export)",
        "run_ids": [run_id],
        "span_count": int(len(df)),
        "latency_ms": {
            "end_to_end": pct(lat[df["span_type"] == "end_to_end"]),
            "by_span_type": {t: pct(lat[df["span_type"] == t])
                             for t in ("thinking", "acting", "tool")},
            "by_node": by_node,
            "by_tool": by_tool,
        },
        "tokens": {"input": tin, "output": tout, "total": tin + tout, "by_node": tokens_by_node,
                   "llm_calls": int(len(llm))},
        "cost": {
            "currency": "USD",
            "model": settings.gemini_model,
            "price_per_1k_input": settings.price_per_1k_input,
            "price_per_1k_output": settings.price_per_1k_output,
            "price_source": "src/config.py (PRICE_PER_1K_INPUT / PRICE_PER_1K_OUTPUT)",
            "estimated_total": round(cost, 6),
            "per_claim_avg": round(cost / claims, 6) if claims else None,
        },
        "quality": quality(),
        "traffic": {"claims_processed": claims,
                    "tool_calls": int((df["span_type"] == "tool").sum()),
                    "llm_calls": int(len(llm))},
        "errors": {"error_spans": errors,
                   "error_rate": round(errors / len(df), 4) if errors is not None else None},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Phoenix-derived golden signals (AC-09)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    report = build(args.run_id)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {args.out} — run {report['run_ids'][0]}, "
          f"{report['traffic']['claims_processed']} claims, "
          f"{report['tokens']['total']} tokens, ${report['cost']['estimated_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
