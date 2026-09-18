"""Agentic RAG over the synthetic policy corpus — SPEC-06.

Chunking is **clause-level**: one chunk per `### §x.y` heading, so a chunk *is* a clause and the
`clause_id` an agent cites is guaranteed to exist rather than being reconstructed by the model. That
single choice is what makes AC-01's clause citation checkable and what the Phase 6 faithfulness
metric scores against.

Embeddings are local Sentence-Transformers — no hosted embedding API (Rule R4).
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from src.config import settings
from src.models import RetrievedClause
from src.tools.registry import logged_tool

log = logging.getLogger(__name__)

# Section headings read "## §4. Own Damage Cover" and clause headings "### §4.2 Collision" —
# the optional trailing dot is load-bearing: without it the section never parses, every clause
# falls back to kind="coverage", and exclusion precedence silently stops working.
HEADING_RE = re.compile(r"^#{2,3}\s+§(\d+(?:\.\d+)?)\.?\s+(.+)$")
COLLECTION = "policy_clauses"

# Section title → clause kind. Exclusion precedence (coverage_rules.md §4) depends on this being
# right: a loss matching both a covering clause and an exclusion must resolve to the exclusion.
SECTION_KIND = {
    "definitions": "definition",
    "claims procedure": "procedure",
    "limits and deductibles": "limit",
    "limits": "limit",
    "own damage cover": "coverage",
    "perils covered": "coverage",
    "coverage": "coverage",
    "third-party liability": "coverage",
    "exclusions": "exclusion",
}

_client: Any = None
_embedder: Any = None


def _corpus_checksum() -> str:
    h = hashlib.sha256()
    for p in sorted(settings.policy_corpus_dir.glob("*.md")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def parse_clauses(path: Path) -> list[dict[str, Any]]:
    """Split one policy document into clause-level chunks with stable ids."""
    product = path.stem
    clauses: list[dict[str, Any]] = []
    section = ""
    current: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            current["text"] = "\n".join(body).strip()
            if current["text"]:
                clauses.append(dict(current))

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        m = HEADING_RE.match(stripped)
        if m and stripped.startswith("## §"):  # section heading
            flush()
            current, body = None, []
            section = m.group(2).strip().rstrip(".").lower()
            continue
        if m and stripped.startswith("### §"):  # clause heading
            flush()
            num, title = m.group(1), m.group(2).strip()
            current = {
                "clause_id": f"{product} §{num}",
                "clause_title": title,
                "section": section,
                "product": product,
                "kind": SECTION_KIND.get(section, "coverage"),
                "path": str(path.relative_to(settings.root)),
            }
            body = []
            continue
        if current is not None:
            body.append(line)

    flush()
    return clauses


def load_all_clauses() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(settings.policy_corpus_dir.glob("*.md")):
        if p.name == "coverage_rules.md":
            continue  # served as the MCP resource, not retrieved as a policy clause
        out.extend(parse_clauses(p))
    return out


def _get_embedder() -> Any:
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer(settings.embedding_model)
    return _embedder


def _get_collection(create: bool = True) -> Any:
    global _client
    import chromadb

    if _client is None:
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(settings.chroma_path))
    if create:
        return _client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    return _client.get_collection(COLLECTION)


def build_index(force: bool = False) -> dict[str, Any]:
    """Idempotent. Rebuilds when the corpus checksum changes, so a corpus edit can't go stale."""
    checksum = _corpus_checksum()
    col = _get_collection()

    if not force and col.count() > 0:
        existing = col.get(limit=1, include=["metadatas"])
        metas = existing.get("metadatas") or []
        if metas and metas[0].get("corpus_checksum") == checksum:
            return {"status": "current", "clauses": col.count(), "checksum": checksum}

    clauses = load_all_clauses()
    if not clauses:
        return {"status": "empty", "clauses": 0, "checksum": checksum}

    if col.count() > 0:
        global _client
        _client.delete_collection(COLLECTION)
        col = _get_collection()

    embedder = _get_embedder()
    docs = [f"{c['clause_title']}. {c['text']}" for c in clauses]
    embeddings = embedder.encode(docs, show_progress_bar=False, normalize_embeddings=True)

    col.add(
        ids=[c["clause_id"] for c in clauses],
        documents=docs,
        embeddings=[e.tolist() for e in embeddings],
        metadatas=[{**c, "corpus_checksum": checksum} for c in clauses],
    )
    return {"status": "built", "clauses": len(clauses), "checksum": checksum}


@logged_tool(
    name="retrieve_policy_clauses",
    source="rag",
    description="Retrieve policy clauses relevant to a loss, filtered by product and clause kind.",
    input_schema={
        "query": "str", "product": "str | None", "kind": "str | None", "k": "int",
    },
    output_model=RetrievedClause,
)
async def retrieve_policy_clauses(
    query: str,
    product: str | None = None,
    kind: str | None = None,
    k: int = 4,
) -> list[RetrievedClause]:
    """Retrieval-in-the-loop over the policy corpus. Returns clause ids the agent must cite."""
    try:
        build_index()
        col = _get_collection()
    except Exception as exc:  # noqa: BLE001 - degrade to no retrieval (NFR-04)
        log.warning("RAG index unavailable: %s", exc)
        return []

    where: dict[str, Any] = {}
    clauses = [c for c in ({"product": product} if product else {}, {"kind": kind} if kind else {}) if c]
    if len(clauses) == 1:
        where = clauses[0]
    elif len(clauses) == 2:
        where = {"$and": clauses}

    try:
        vec = _get_embedder().encode([query], normalize_embeddings=True)[0].tolist()
        res = col.query(
            query_embeddings=[vec],
            n_results=max(k, 1),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("RAG query failed: %s", exc)
        return []

    out: list[RetrievedClause] = []
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for meta, dist in zip(metas, dists):
        out.append(
            RetrievedClause(
                clause_id=meta["clause_id"],
                clause_title=meta["clause_title"],
                text=meta.get("text", ""),
                kind=meta.get("kind", "coverage"),
                product=meta.get("product", ""),
                score=round(1.0 - float(dist), 4),
            )
        )
    return out
