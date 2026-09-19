"""Tier 1 — short-term memory: the LangGraph SQLite checkpointer (SPEC-04 §2.1).

Keyed by thread_id. Gives in-conversation context and resumability, which is the first half of
AC-05 ("uses facts stated earlier in the interaction").
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.config import settings


def build_checkpointer() -> Any:
    """An AsyncSqliteSaver over the SQLite file. No external DB service (Rule R4).

    Async because the graph runs via `ainvoke` (NFR-04). The sync `SqliteSaver` raises
    NotImplementedError on every async checkpoint call, which degraded every live run —
    docs/failure-analysis.md F-01. The aiosqlite connection is opened lazily on first use, inside
    the running event loop. Outside a loop (compile-only callers such as `src.main graph`) the
    sync saver over the same file is returned — nothing invokes the graph there.
    """
    import asyncio

    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver(sqlite3.connect(str(settings.checkpoint_path), check_same_thread=False))

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    return AsyncSqliteSaver(aiosqlite.connect(str(settings.checkpoint_path)))


def thread_config(thread_id: str, **extra: Any) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, **extra}}


def checkpoint_count() -> int:
    """Used by the Phase 2 gate ('a checkpoint row exists after a run') and by the memory test."""
    if not settings.checkpoint_path.exists():
        return 0
    conn = sqlite3.connect(str(settings.checkpoint_path))
    try:
        cur = conn.execute("SELECT COUNT(*) FROM checkpoints")
        return int(cur.fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()
