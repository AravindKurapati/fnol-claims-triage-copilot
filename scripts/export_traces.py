"""Export Phoenix spans → `traces/phoenix_spans.parquet` (SPEC-07 §2.2, §7.2 "Trace export").

    python scripts/export_traces.py               # export every span in the Phoenix project
    python scripts/export_traces.py --run-id run-…  # only the traces of one run

The export is **cumulative**: spans already in the committed parquet are kept and new ones are
merged in (deduplicated on span id). That matters for Rule R2 — `docs/failure-analysis.md` cites
span ids from the *failing* runs as well as from the runs that verify the fix, and every one of them
must still resolve after the fixes are applied and the trace is re-exported.

Also writes `traces/trace_manifest.json` — one row per run_id with its span counts, node names and
tool names — so a reviewer can see what the parquet contains without opening it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402
from src.security.masking import mask_text  # noqa: E402

TRACES_DIR = ROOT / "traces"
PARQUET = TRACES_DIR / "phoenix_spans.parquet"
MANIFEST = TRACES_DIR / "trace_manifest.json"
# Never masked: these are what docs/failure-analysis.md cites (Rule R2).
ID_COLUMNS = {"context.span_id", "context.trace_id", "parent_id", "run_id",
              "attributes.fnol.run_id", "attributes.fnol.claim_id", "attributes.fnol.thread_id",
              "attributes.session.id"}


# ───────────────────────────── Phoenix server ────────────────────────────────


def phoenix_up(endpoint: str | None = None) -> bool:
    url = (endpoint or settings.phoenix_collector_endpoint).rstrip("/") + "/healthz"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 - localhost only
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def ensure_phoenix(wait_s: float = 90.0) -> bool:
    """Start a local Phoenix server (UI + OTLP collector on :6006) if one is not already running."""
    if phoenix_up():
        return True
    log_path = ROOT / "var" / "phoenix_server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    with log_path.open("ab") as fh:
        subprocess.Popen(  # noqa: S603 - fixed argv, our own interpreter
            [sys.executable, "-m", "phoenix.server.main", "serve"],
            cwd=str(ROOT), stdout=fh, stderr=fh, creationflags=flags,
            start_new_session=sys.platform != "win32",
        )
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if phoenix_up():
            return True
        time.sleep(1.5)
    return False


def _gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        settings.phoenix_collector_endpoint.rstrip("/") + "/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - localhost only
        return json.loads(resp.read())


def register_model_prices() -> list[str]:
    """Teach Phoenix the price of the Gemini models we run, so its UI cost column is populated.

    Phoenix prices a span when it is ingested, from its built-in model table — which has no entry
    for the current flash-lite models, so the UI showed "Total Cost $0". The prices are the same
    auditable basis `scripts/golden_signals.py` uses (`src/config.py`), keeping the screenshot and
    the report consistent. Idempotent: a model already registered is left alone.
    """
    edges = _gql("{ generativeModels(first: 1000) { edges { node { name } } } }")["data"][
        "generativeModels"]["edges"]
    existing = {e["node"]["name"] for e in edges}
    created = []
    prices = {settings.gemini_model: (settings.price_per_1k_input * 1000,
                                      settings.price_per_1k_output * 1000)}
    if settings.gemini_judge_model not in prices:
        prices[settings.gemini_judge_model] = (settings.judge_price_per_1k_input * 1000,
                                               settings.judge_price_per_1k_output * 1000)
    for model, (inp, out) in prices.items():
        if model in existing:
            continue
        res = _gql(
            "mutation($i: CreateModelMutationInput!) { createModel(input: $i) { model { name } } }",
            {"i": {"name": model, "provider": "google", "namePattern": f"(?i)^{model}$",
                   "costs": [
                       {"tokenType": "input", "kind": "PROMPT", "costPerMillionTokens": inp},
                       {"tokenType": "output", "kind": "COMPLETION", "costPerMillionTokens": out},
                   ]}},
        )
        if not res.get("errors"):
            created.append(model)
    return created


# ─────────────────────────────── export ──────────────────────────────────────


def _client() -> Any:
    from phoenix.client import Client  # Phoenix ≥ 12: the successor of px.Client()

    return Client(base_url=settings.phoenix_collector_endpoint)


def fetch_spans(run_id: str | None = None) -> Any:
    """All spans of the project (optionally: only traces whose root carries `run_id`).

    Equivalent of the brief's `px.Client().get_spans_dataframe()` on the current Phoenix client.
    """
    df = _client().spans.get_spans_dataframe(
        project_identifier=settings.phoenix_project_name, limit=100_000, timeout=120
    )
    if df is None or df.empty:
        raise SystemExit("Phoenix returned no spans — run `python -m src.main trace` first.")
    df = df.reset_index(drop="context.span_id" in df.columns)
    if run_id:
        roots = df[(df["name"] == "triage_claim") & (_run_ids(df) == run_id)]
        df = df[df["context.trace_id"].isin(set(roots["context.trace_id"]))]
    return df


def _run_ids(df: Any) -> Any:
    """run_id per span: own attribute, else the root span's attribute for the same trace."""
    import pandas as pd

    own = pd.Series([None] * len(df), index=df.index, dtype="object")
    for col in ("attributes.fnol.run_id", "attributes.fnol"):
        if col in df.columns:
            vals = df[col].map(lambda v: v.get("run_id") if isinstance(v, dict) else v)
            own = own.fillna(vals)
    by_trace = (
        pd.DataFrame({"t": df["context.trace_id"], "r": own}).dropna().drop_duplicates("t")
        .set_index("t")["r"]
    )
    return own.fillna(df["context.trace_id"].map(by_trace))


def _normalise(df: Any) -> Any:
    """Parquet-safe: nested/heterogeneous object columns become JSON strings; add convenience cols."""
    import pandas as pd

    df = df.copy()
    df["run_id"] = _run_ids(df)
    # Our own span attributes (fnol.run_id, fnol.claim_id, fnol.agent, …) arrive as one nested dict;
    # flatten them so they are queryable columns in the parquet.
    for nested in ("attributes.fnol", "attributes.tool"):
        if nested in df.columns:
            expanded = pd.json_normalize(
                df[nested].map(lambda v: v if isinstance(v, dict) else {}).tolist()
            )
            expanded.index = df.index
            for key in expanded.columns:
                df[f"{nested}.{key}"] = expanded[key]
            df = df.drop(columns=[nested])
    if "start_time" in df.columns and "end_time" in df.columns:
        st = pd.to_datetime(df["start_time"], utc=True)
        et = pd.to_datetime(df["end_time"], utc=True)
        df["start_time"], df["end_time"] = st, et
        df["latency_ms"] = (et - st).dt.total_seconds() * 1000.0
    for col in df.columns:
        if df[col].dtype == "object" or str(df[col].dtype).startswith("str"):
            if df[col].map(lambda v: isinstance(v, (dict, list, tuple))).any():
                df[col] = df[col].map(
                    lambda v: json.dumps(v, default=str) if isinstance(v, (dict, list, tuple))
                    else (None if v is None else str(v))
                )
            else:
                df[col] = df[col].map(lambda v: None if v is None or v != v else str(v))
            # Second masking layer: spans recorded before the exporter fix (F-06) are masked here,
            # so no committed trace ever carries a plaintext identifier (NFR-05). Identity columns
            # are exempt — a hex span id can contain a 10-digit run the phone regex would star out.
            if col not in ID_COLUMNS:
                df[col] = df[col].map(lambda v: mask_text(v) if isinstance(v, str) else v)
    return df


def merge_with_committed(new: Any) -> Any:
    import pandas as pd

    if not PARQUET.exists():
        return new
    old = pd.read_parquet(PARQUET)
    merged = pd.concat([old, new], ignore_index=True, sort=False)
    merged = merged.drop_duplicates(subset=["context.span_id"], keep="last")
    for col in merged.columns:  # re-harmonise dtypes after concat; re-mask older rows too
        if merged[col].dtype == "object" or str(merged[col].dtype).startswith("str"):
            merged[col] = merged[col].map(
                lambda v: None if v is None or v != v
                else (str(v) if col in ID_COLUMNS else mask_text(str(v))))
    return merged.sort_values("start_time").reset_index(drop=True)


def write_manifest(df: Any) -> dict[str, Any]:
    from src.tools import registry  # noqa: F401 - populates the registry via tool modules

    runs: dict[str, Any] = {}
    kind_col = "span_kind" if "span_kind" in df.columns else None
    for run_id, g in df.groupby(df["run_id"].fillna("unknown")):
        kinds = g[kind_col].value_counts().to_dict() if kind_col else {}
        runs[run_id] = {
            "traces": int(g["context.trace_id"].nunique()),
            "spans": int(len(g)),
            "span_kinds": {str(k): int(v) for k, v in kinds.items()},
            "claims": sorted(g.loc[g["name"] == "triage_claim", "context.trace_id"].map(
                lambda t, g=g: _claim_of(g, t)).dropna().unique().tolist()),
            "tool_spans": sorted(g.loc[g.get(kind_col) == "TOOL", "name"].unique().tolist())
            if kind_col else [],
            "first_span": str(g["start_time"].min()),
        }
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Phoenix project " + settings.phoenix_project_name,
        "producer": "scripts/export_traces.py",
        "file": str(PARQUET.relative_to(ROOT)).replace("\\", "/"),
        "total_spans": int(len(df)),
        "runs": runs,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _claim_of(g: Any, trace_id: str) -> str | None:
    col = "attributes.fnol.claim_id" if "attributes.fnol.claim_id" in g.columns else None
    if col is None:
        return None
    row = g[(g["context.trace_id"] == trace_id) & (g["name"] == "triage_claim")]
    return None if row.empty else row[col].iloc[0]


def export(run_id: str | None = None, *, cumulative: bool = True) -> dict[str, Any]:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    df = _normalise(fetch_spans(run_id))
    if cumulative:
        df = merge_with_committed(df)
    df.to_parquet(PARQUET, index=False)
    manifest = write_manifest(df)
    return {"spans": len(df), "file": str(PARQUET), "runs": list(manifest["runs"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--fresh", action="store_true", help="overwrite rather than merge")
    args = ap.parse_args()
    if not phoenix_up():
        print("Phoenix is not running at", settings.phoenix_collector_endpoint)
        return 1
    out = export(args.run_id, cumulative=not args.fresh)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
