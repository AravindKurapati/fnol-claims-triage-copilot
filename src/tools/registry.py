"""The single tool registry — one place every tool is registered and wrapped (design.md §D4).

Why this exists: AC-07 requires that tool names in `logs/tool_calls.jsonl` *reconcile with the
agent/MCP code*. Routing every tool — native, MCP-adapted and RAG — through one registry makes that
true by construction: the logged name **is** the registry name. It also means Phase 4 changes one
sink instead of twenty call sites, and Phase 6's tool-contract test enumerates the registry rather
than a hand-maintained list.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from src.observability.tool_logger import log_tool_call, timed


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str = ""
    source: str = "native"  # native | mcp | rag
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_model: Any = None


_REGISTRY: dict[str, ToolSpec] = {}


def logged_tool(
    *,
    name: str,
    description: str = "",
    source: str = "native",
    input_schema: dict[str, Any] | None = None,
    output_model: Any = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a tool and wrap it with AC-07 logging. Works for sync and async callables."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                with timed() as t:
                    try:
                        result = await fn(*args, **kwargs)
                        status = "ok"
                    except asyncio.TimeoutError:
                        result, status = {"error": "timeout"}, "timeout"
                        raise
                    except Exception as exc:  # noqa: BLE001 - logged, then re-raised
                        result, status = {"error": f"{type(exc).__name__}: {exc}"}, "error"
                        raise
                    finally:
                        log_tool_call(
                            tool_name=name,
                            args=_bind(fn, args, kwargs),
                            result=result if status == "ok" else result,
                            latency_ms=t.ms,
                            status=status,
                        )
                return result

            wrapper: Callable[..., Any] = awrapper
        else:

            @functools.wraps(fn)
            def swrapper(*args: Any, **kwargs: Any) -> Any:
                with timed() as t:
                    try:
                        result = fn(*args, **kwargs)
                        status = "ok"
                    except Exception as exc:  # noqa: BLE001 - logged, then re-raised
                        result, status = {"error": f"{type(exc).__name__}: {exc}"}, "error"
                        raise
                    finally:
                        log_tool_call(
                            tool_name=name,
                            args=_bind(fn, args, kwargs),
                            result=result,
                            latency_ms=t.ms,
                            status=status,
                        )
                return result

            wrapper = swrapper

        register(
            ToolSpec(
                name=name,
                fn=wrapper,
                description=description or (fn.__doc__ or "").strip().split("\n")[0],
                source=source,
                input_schema=input_schema or {},
                output_model=output_model,
            )
        )
        wrapper.__tool_name__ = name  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _bind(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Name positional arguments so the log records `{"policy_number": ...}`, not `{"0": ...}`."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except (TypeError, ValueError):
        return {"args": list(args), "kwargs": kwargs}


def register(spec: ToolSpec) -> None:
    _REGISTRY[spec.name] = spec


def register_external(
    name: str, fn: Callable[..., Any], *, description: str = "", source: str = "mcp",
    input_schema: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    """Wrap an already-built tool (e.g. an MCP-adapted LangChain tool) in AC-07 logging.

    This is how MCP tools get the same treatment as native ones without being redefined — the name
    passed here is the MCP tool's own name, which is what keeps the log reconcilable (SPEC-05 §2.2).
    """
    wrapped = logged_tool(
        name=name, description=description, source=source, input_schema=input_schema
    )(fn)
    return wrapped


def get(name: str) -> ToolSpec:
    return _REGISTRY[name]


def list_tools() -> list[str]:
    """The authoritative tool-name list. AC-07 reconciliation and P6 contract tests read this."""
    return sorted(_REGISTRY)


def all_specs() -> list[ToolSpec]:
    return [_REGISTRY[n] for n in list_tools()]


def clear() -> None:
    """Test hook only."""
    _REGISTRY.clear()
