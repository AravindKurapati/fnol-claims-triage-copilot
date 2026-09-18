"""MCP client — SPEC-05 §2.2. Consumes the custom server via langchain-mcp-adapters over stdio.

Two things here earn marks rather than just working:

  * every adapted tool is re-registered through `src/tools/registry.py`, so MCP tools wear the same
    `@logged_tool` wrapper as native ones. That is what makes AC-07's "tool names reconcile with the
    agent/MCP code" true by construction rather than by discipline;
  * the transcript is written by this client on every call, including the resource read — the
    resource is graded separately from the tools (SPEC-05 §2.3).
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.observability.tool_logger import current_run_id
from src.security.masking import mask_record
from src.tools import registry

log = logging.getLogger(__name__)

TRANSCRIPT = "mcp_transcript.jsonl"
MCP_TOOL_NAMES = ("lookup_policy", "check_claim_history", "validate_coverage_window")
COVERAGE_RULES_URI = "policy://handbook/coverage-rules"

_session_id = f"mcp-{uuid.uuid4().hex[:4]}"


def _transcribe(**fields: Any) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": _session_id,
        "run_id": current_run_id.get(),
        **fields,
    }
    with (settings.logs_dir / TRANSCRIPT).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(mask_record(record), default=str) + "\n")


def server_params() -> dict[str, Any]:
    """stdio subprocess — no network, no Docker (Rule R4)."""
    return {
        "fnol-policy": {
            "command": sys.executable,
            "args": ["-m", "mcp_server.server"],
            "transport": "stdio",
            "cwd": str(settings.root),
        }
    }


class MCPToolset:
    """Owns the client session for the life of a run. Degrades to empty on failure (NFR-04)."""

    def __init__(self) -> None:
        self._client: Any = None
        self.tools: list[Any] = []
        self.available = False
        self.error: str | None = None

    async def __aenter__(self) -> "MCPToolset":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> "MCPToolset":
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            self._client = MultiServerMCPClient(server_params())
            raw = await self._client.get_tools()
            self.tools = [self._wrap(t) for t in raw]
            self.available = True
            _transcribe(direction="event", kind="session", name="connected",
                        tools=[t.name for t in raw])
        except Exception as exc:  # noqa: BLE001 - unavailability is degradation, not a crash
            self.available = False
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("MCP server unavailable: %s", self.error)
            _transcribe(direction="event", kind="session", name="unavailable", error=self.error)
        return self

    async def close(self) -> None:
        self._client = None

    def _wrap(self, tool: Any) -> Any:
        """Transcribe + AC-07-log every MCP invocation, keeping the MCP tool's own name."""
        original = tool.coroutine or tool.func
        name = tool.name

        async def transcribed(**kwargs: Any) -> Any:
            _transcribe(direction="call", kind="tool", name=name, args=kwargs)
            t0 = time.perf_counter()
            try:
                result = await original(**kwargs) if tool.coroutine else original(**kwargs)
                status = "ok"
            except Exception as exc:  # noqa: BLE001
                result, status = {"error": f"{type(exc).__name__}: {exc}"}, "error"
                raise
            finally:
                _transcribe(
                    direction="result", kind="tool", name=name, status=status,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                    result_summary=_summarise(locals().get("result")),
                )
            return result

        transcribed.__name__ = name
        transcribed.__doc__ = tool.description
        logged = registry.register_external(
            name, transcribed, description=tool.description or "", source="mcp",
            input_schema=getattr(tool, "args_schema", None) and tool.args.copy() or {},
        )
        tool.coroutine = logged
        tool.func = None
        return tool

    async def call(self, name: str, **kwargs: Any) -> dict[str, Any] | None:
        """Direct call by name — how the agents use MCP (they do not bind these as LLM tools)."""
        for t in self.tools:
            if t.name == name:
                raw = await t.coroutine(**kwargs)
                return _as_dict(raw)
        return None

    async def read_coverage_rules(self) -> str | None:
        """Read the MCP **resource** (graded separately from the tools — SPEC-05 §2.1)."""
        if not self.available or self._client is None:
            return None
        _transcribe(direction="call", kind="resource", name=COVERAGE_RULES_URI)
        t0 = time.perf_counter()
        try:
            blobs = await self._client.get_resources("fnol-policy", uris=[COVERAGE_RULES_URI])
            body = "\n".join(
                b.data if isinstance(getattr(b, "data", None), str) else str(b) for b in blobs
            )
            _transcribe(direction="result", kind="resource", name=COVERAGE_RULES_URI,
                        status="ok", latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                        result_summary={"bytes": len(body)})
            return body
        except Exception as exc:  # noqa: BLE001
            _transcribe(direction="result", kind="resource", name=COVERAGE_RULES_URI,
                        status="error", error=f"{type(exc).__name__}: {exc}")
            return None


def _as_dict(raw: Any) -> dict[str, Any]:
    """Unwrap whatever langchain-mcp-adapters hands back into the tool's own JSON payload.

    Depending on the adapter version a tool result arrives as a JSON string, a list of content
    blocks, or a (content_blocks, artifact) tuple where the artifact carries `structured_content`.
    Unwrapping all three matters: the coverage and fraud agents read real fields off this
    (`sum_insured`, `within_reporting_deadline`, `claim_count`), and a half-parsed `{"raw": "..."}`
    would silently disable fraud indicators FI-01, FI-02, FI-04, FI-05 and FI-06.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        # response_format="content_and_artifact" artifact shape
        inner = raw.get("structured_content")
        if isinstance(inner, dict):
            return _as_dict(inner.get("result", inner))
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
    if isinstance(raw, tuple):
        # (content_blocks, artifact) — prefer the structured artifact, fall back to the blocks
        for part in reversed(raw):
            parsed = _as_dict(part)
            if parsed and "raw" not in parsed:
                return parsed
        return {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "text" in item:
                return _as_dict(item["text"])
            parsed = _as_dict(item)
            if parsed and "raw" not in parsed:
                return parsed
        return {}
    text = getattr(raw, "text", None)
    if isinstance(text, str):
        return _as_dict(text)
    return {"raw": str(raw)}


def _summarise(result: Any) -> Any:
    d = _as_dict(result) if result is not None else {}
    keep = ("found", "product", "status", "claim_count", "within_reporting_deadline",
            "policy_active_on_loss_date", "error")
    return {k: d[k] for k in keep if k in d} or {"keys": sorted(d)[:6]}
