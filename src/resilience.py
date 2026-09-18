"""Timeout / retry / typed degradation for every model and tool call (NFR-04).

A degraded run still produces a decision and an audit record. A crashed run produces neither, which
is worth nothing under the Evidence-in-Repo rule — so nothing in this project is allowed to raise
out of a node.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel

from src.config import settings

log = logging.getLogger(__name__)
T = TypeVar("T")


class ErrorRecord(BaseModel):
    component: str
    kind: str  # "timeout" | "error" | "schema" | "unavailable"
    detail: str
    attempts: int


class DegradedResult(BaseModel):
    """Returned instead of raising, so the caller can keep going and mark the run degraded."""

    error: ErrorRecord


async def with_resilience(
    fn: Callable[[], Awaitable[T]],
    *,
    component: str,
    timeout_s: float | None = None,
    max_retries: int | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T | DegradedResult:
    """Run `fn` with a timeout and bounded retry; degrade instead of raising."""
    timeout = timeout_s if timeout_s is not None else settings.tool_timeout_s
    retries = max_retries if max_retries is not None else settings.max_retries

    last_kind, last_detail = "error", "unknown"
    for attempt in range(1, retries + 2):  # 1 initial + `retries` retries
        try:
            return await asyncio.wait_for(fn(), timeout=timeout)
        except asyncio.TimeoutError:
            last_kind, last_detail = "timeout", f"exceeded {timeout}s"
            log.warning("%s timed out (attempt %d/%d)", component, attempt, retries + 1)
        except retry_on as exc:  # noqa: BLE001 - deliberate: degradation is the contract
            last_kind, last_detail = "error", f"{type(exc).__name__}: {exc}"
            log.warning("%s failed (attempt %d/%d): %s", component, attempt, retries + 1, exc)

        if attempt <= retries:
            backoff = min(2 ** (attempt - 1), 8) * (0.5 + random.random() / 2)
            await asyncio.sleep(backoff)

    return DegradedResult(
        error=ErrorRecord(
            component=component, kind=last_kind, detail=last_detail, attempts=retries + 1
        )
    )


def is_degraded(value: Any) -> bool:
    return isinstance(value, DegradedResult)
