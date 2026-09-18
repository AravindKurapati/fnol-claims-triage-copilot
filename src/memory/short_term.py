"""Tier 1 — short-term memory: the LangGraph SQLite checkpointer (SPEC-04 §2.1).

Keyed by thread_id. Gives in-conversation context and resumability, which is the first half of
AC-05 ("uses facts stated earlier in the interaction").
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.config import settings


def build_checkpointer() -> Any:
    """A SqliteSaver over the committed-path SQLite file. No external DB service (Rule R4)."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.checkpoint_path), check_same_thread=False)
    return SqliteSaver(conn)


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
