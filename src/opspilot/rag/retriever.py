"""Top-k cosine-similarity retrieval over rag.doc_chunks (pgvector ``<=>`` operator)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.config import get_settings
from opspilot.db.models import DocChunk
from opspilot.rag.embeddings import Embedder, get_embedder


@dataclass(frozen=True)
class Hit:
    doc_name: str
    title: str
    section: str | None
    content: str
    score: float  # cosine similarity (1 - cosine distance)

    def to_dict(self) -> dict:
        return asdict(self)


def search(session: Session, query: str, k: int | None = None,
           embedder: Embedder | None = None) -> list[Hit]:  # fmt: skip
    """Return the ``k`` most similar chunks to ``query``."""
    k = k or get_settings().rag_top_k
    qvec = (embedder or get_embedder()).embed_query(query)
    distance = DocChunk.embedding.cosine_distance(qvec).label("distance")
    rows = session.execute(
        select(DocChunk.doc_name, DocChunk.title, DocChunk.section, DocChunk.content, distance)
        .order_by(distance)
        .limit(k)
    ).all()
    return [Hit(r.doc_name, r.title, r.section, r.content, round(1 - float(r.distance), 4))
            for r in rows]  # fmt: skip


def unique_docs(hits: list[Hit]) -> list[str]:
    """Doc names in rank order without duplicates (used for citations and hit@k)."""
    seen: list[str] = []
    for h in hits:
        if h.doc_name not in seen:
            seen.append(h.doc_name)
    return seen
