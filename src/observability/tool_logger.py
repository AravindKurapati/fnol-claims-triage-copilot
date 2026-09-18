"""Tool-invocation logging — AC-07.

PHASE 4 SEAM. The decorator below already wraps every tool in the registry, and already records the
full AC-07 record shape. Phase 4's job is the marked TODO only: attach the real Phoenix span id.
Nothing about the call sites changes.

Record (AC-07 / SPEC-07 §2.3):
    {timestamp, agent, tool_name, args, result, latency_ms, status, run_id, span_id}
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.security.masking import mask_record

TOOL_LOG = "tool_calls.jsonl"

# Set by the graph before each node runs, so a tool call can name the agent that made it.
current_agent: ContextVar[str] = ContextVar("current_agent", default="unknown")
current_run_id: ContextVar[str] = ContextVar("current_run_id", default="unset")


def _summarise(value: Any, limit: int = 600) -> Any:
    """Keep the log readable and bounded without losing what a reviewer needs to reconcile."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) or len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return {"n": len(value), "items": [_summarise(v, 160) for v in value[:5]]}
    if isinstance(value, dict):
        return {k: _summarise(v, 200) for k, v in list(value.items())[:12]}
    if hasattr(value, "model_dump"):
        return _summarise(value.model_dump(), limit)
    return str(value)[:limit]


def log_tool_call(
    *,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    latency_ms: float,
    status: str,
    agent: str | None = None,
) -> None:
    """Append one AC-07 record. Masked before write (NFR-05)."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    # --- PHASE 4 TODO: replace with the live Phoenix span id ---
    #     from src.observability.tracing import span_ids;  _, span_id = span_ids()
    span_id = None

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent or current_agent.get(),
        "tool_name": tool_name,
        "args": mask_record(_summarise(args)),
        "result": mask_record(_summarise(result)),
        "latency_ms": round(latency_ms, 2),
        "status": status,
        "run_id": current_run_id.get(),
        "span_id": span_id,
    }
    with (settings.logs_dir / TOOL_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


class timed:
    """Small helper so tools measure latency identically everywhere."""

    def __enter__(self) -> "timed":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self._t0) * 1000

    @property
    def ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000
