"""Cost / latency dashboard — AC-09, SPEC-08 §2.2.

    python scripts/build_dashboard.py [--run-id run-…]

Produces the required pair **from the same run**:

  * `reports/dashboard_data.csv` — the span-level data (Phoenix `get_spans_dataframe()` export via
    `traces/phoenix_spans.parquet`), one row per span with latency, tokens and cost;
  * `reports/dashboard.png` — a screenshot of the **Phoenix UI** project view at localhost:6006
    (trace list with latency / token / cost columns and the project's summary stats), captured
    headlessly with Playwright so the PNG has producing code (Rule R1);

plus `reports/dashboard_chart.png`, the same CSV drawn with matplotlib, so every number in the
screenshot can be reconciled with the data file.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from golden_signals import annotate, load_spans  # noqa: E402

from src.config import settings  # noqa: E402

CSV = ROOT / "reports/dashboard_data.csv"
PNG = ROOT / "reports/dashboard.png"
CHART = ROOT / "reports/dashboard_chart.png"

COLUMNS = ["run_id", "context.trace_id", "context.span_id", "parent_id", "name", "span_kind",
           "span_type", "node", "start_time", "end_time", "latency_ms", "tokens_in", "tokens_out",
           "cost_usd", "status_code"]


def write_csv(run_id: str | None) -> tuple[str, int]:
    df, run_id = load_spans(run_id)
    df = annotate(df)
    cols = [c for c in COLUMNS if c in df.columns]
    df[cols].sort_values("start_time").to_csv(CSV, index=False)
    return run_id, len(df)


def _project_id() -> str | None:
    q = json.dumps({"query": "{ projects { edges { node { id name } } } }"}).encode()
    req = urllib.request.Request(settings.phoenix_collector_endpoint.rstrip("/") + "/graphql",
                                 data=q, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - localhost only
        data = json.loads(resp.read())
    for edge in data["data"]["projects"]["edges"]:
        if edge["node"]["name"] == settings.phoenix_project_name:
            return edge["node"]["id"]
    return None


def screenshot() -> bool:
    """Capture the Phoenix project view. Needs Phoenix running and `playwright install chromium`."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed — skipping the Phoenix screenshot")
        return False
    pid = _project_id()
    if pid is None:
        print(f"Phoenix project {settings.phoenix_project_name!r} not found")
        return False
    url = f"{settings.phoenix_collector_endpoint.rstrip('/')}/projects/{pid}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1050}, device_scale_factor=1)
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(4_000)  # let the stats + table hydrate
        page.screenshot(path=str(PNG), full_page=False)
        browser.close()
    print(f"screenshot {url} → {PNG.relative_to(ROOT)}")
    return True


def chart() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    df = pd.read_csv(CSV)
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(f"FNOL triage — Phoenix spans for {df['run_id'].iloc[0]} "
                 f"({(df['span_type'] == 'end_to_end').sum()} claims)", fontsize=13)

    e2e = df[df["span_type"] == "end_to_end"].sort_values("start_time")
    axes[0, 0].bar(range(len(e2e)), e2e["latency_ms"] / 1000, color="#4c72b0")
    axes[0, 0].set_title("End-to-end latency per claim (s)")
    axes[0, 0].set_xlabel("claim (in run order)")

    lat = df[df["span_type"].isin(["thinking", "acting", "tool"])]
    groups = [lat.loc[lat["span_type"] == t, "latency_ms"] for t in ("thinking", "acting", "tool")]
    axes[0, 1].boxplot(groups, showfliers=False)
    axes[0, 1].set_xticks([1, 2, 3], ["thinking (LLM)", "acting (node)", "tool"])
    axes[0, 1].set_title("Latency by span type (ms)")

    tok = df[df["span_type"] == "thinking"].groupby(df["node"].fillna("unattributed"))[
        ["tokens_in", "tokens_out"]].sum()
    tok.plot(kind="bar", stacked=True, ax=axes[1, 0], color=["#55a868", "#c44e52"])
    axes[1, 0].set_title("Tokens by agent (in / out)")
    axes[1, 0].set_xlabel("")

    cost = df.groupby(df["node"].fillna("unattributed"))["cost_usd"].sum().sort_values()
    cost.plot(kind="barh", ax=axes[1, 1], color="#8172b2")
    axes[1, 1].set_title(f"Estimated cost by agent (USD) — total ${cost.sum():.5f}")

    fig.tight_layout()
    fig.savefig(CHART, dpi=110)
    plt.close(fig)
    print(f"chart → {CHART.relative_to(ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="AC-09 dashboard: CSV + Phoenix screenshot")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--no-screenshot", action="store_true")
    args = ap.parse_args()
    CSV.parent.mkdir(parents=True, exist_ok=True)
    run_id, n = write_csv(args.run_id)
    print(f"wrote {CSV.relative_to(ROOT)} — {n} spans from {run_id}")
    chart()
    if not args.no_screenshot and not screenshot():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
