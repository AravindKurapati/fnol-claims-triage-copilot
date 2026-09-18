"""Tier 2 — long-term / semantic memory (SPEC-04 §2.2).

This is the half of AC-05 that is actually hard: *cross-session* recall. The store is a SQLite file
namespaced per claimant, so a fact written in one process is recalled by a different process later.
The persistence test (tests/test_memory_persistence.py) proves exactly that.

Two rules keep this safe:
  * the namespace key is the **masked** claimant id, and every stored `text` is masked — an
    unmasked identifier must never reach the store (NFR-05);
  * nothing derived from quarantined narrative is written without passing through masking first,
    because memory is injected into prompts as *trusted* content (SPEC-03 §2.4). That is precisely
    why untrusted text may not be written into it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.config import settings
from src.security.masking import mask_text

MemoryKind = Literal["fact", "preference", "prior_claim", "contact_pref", "open_issue"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    claim_id    TEXT,
    created_at  TEXT NOT NULL,
    source_run_id TEXT,
    embedding   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ns ON memories(namespace);
"""

_embedder: Any = None


class MemoryRecord(BaseModel):
    kind: MemoryKind
    text: str
    claim_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_run_id: str | None = None


def namespace(claimant_id: str) -> str:
    """Namespaced per claimant, on the MASKED id — the store never holds a plaintext identifier."""
    return f"claimant/{mask_text(claimant_id)}"


def _connect() -> sqlite3.Connection:
    settings.memory_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.memory_path), check_same_thread=False)
    conn.executescript(_SCHEMA)
    return conn


def _get_embedder() -> Any:
    """Same local Sentence-Transformers model as the RAG tool — one load, one cost story."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer(settings.embedding_model)
    return _embedder


def _embed(text: str) -> list[float] | None:
    try:
        return _get_embedder().encode([text], normalize_embeddings=True)[0].tolist()
    except Exception:  # noqa: BLE001 - recall degrades to recency, never fails the run
        return None


async def remember(claimant_id: str, record: MemoryRecord) -> str:
    """Write one memory. Text is masked on the way in — always."""
    ns = namespace(claimant_id)
    safe_text = mask_text(record.text)
    # Stable across processes on purpose. Python's built-in hash() is salted per process
    # (PYTHONHASHSEED), so using it here gave the same fact a different id on every run and the
    # INSERT OR REPLACE below silently accumulated duplicate rows instead of deduplicating.
    digest = hashlib.sha256(f"{ns}|{record.kind}|{safe_text}".encode()).hexdigest()[:16]
    mem_id = f"{ns}:{record.kind}:{digest}"
    vec = _embed(safe_text)

    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO memories "
            "(id, namespace, kind, text, claim_id, created_at, source_run_id, embedding) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (mem_id, ns, record.kind, safe_text, record.claim_id, record.created_at,
             record.source_run_id, json.dumps(vec) if vec else None),
        )
        conn.commit()
    finally:
        conn.close()
    return mem_id


async def recall(claimant_id: str, query: str, k: int = 5) -> list[MemoryRecord]:
    """Semantic recall, newest-first as the fallback when embeddings are unavailable."""
    ns = namespace(claimant_id)
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT kind, text, claim_id, created_at, source_run_id, embedding "
            "FROM memories WHERE namespace = ? ORDER BY created_at DESC",
            (ns,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    qvec = _embed(query)
    scored: list[tuple[float, MemoryRecord]] = []
    for kind, text, claim_id, created_at, run_id, emb in rows:
        rec = MemoryRecord(kind=kind, text=text, claim_id=claim_id,
                           created_at=created_at, source_run_id=run_id)
        score = 0.0
        if qvec and emb:
            vec = json.loads(emb)
            score = sum(a * b for a, b in zip(qvec, vec))
        scored.append((score, rec))

    if qvec and any(s for s, _ in scored):
        scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored[:k]]


async def forget(claimant_id: str, memory_id: str | None = None) -> int:
    """Erasure hook. `docs/compliance.md` (Phase 5) cites this as the DPDP erasure control."""
    ns = namespace(claimant_id)
    conn = _connect()
    try:
        if memory_id:
            cur = conn.execute(
                "DELETE FROM memories WHERE namespace = ? AND id = ?", (ns, memory_id)
            )
        else:
            cur = conn.execute("DELETE FROM memories WHERE namespace = ?", (ns,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


async def count(claimant_id: str) -> int:
    conn = _connect()
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE namespace = ?", (namespace(claimant_id),)
            ).fetchone()[0]
        )
    finally:
        conn.close()
