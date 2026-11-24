"""Ingest SOP markdown docs into pgvector (rag.doc_chunks).

Run: ``python -m opspilot.rag.ingest`` (idempotent: replaces chunks of re-ingested docs)
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from opspilot.db.models import DocChunk
from opspilot.rag.chunking import Chunk, chunk_markdown
from opspilot.rag.embeddings import Embedder, get_embedder

DEFAULT_SOP_DIR = Path(__file__).resolve().parents[3] / "docs" / "sop"


def load_chunks(sop_dir: Path = DEFAULT_SOP_DIR) -> list[Chunk]:
    """Chunk every ``*.md`` file in ``sop_dir``. Doc name = file name."""
    chunks: list[Chunk] = []
    for path in sorted(sop_dir.glob("*.md")):
        chunks.extend(chunk_markdown(path.name, path.read_text(encoding="utf-8")))
    return chunks


def ingest(session: Session, sop_dir: Path = DEFAULT_SOP_DIR,
           embedder: Embedder | None = None) -> dict[str, int]:  # fmt: skip
    """Embed and store chunks; returns {doc_name: n_chunks}. Caller commits."""
    embedder = embedder or get_embedder()
    chunks = load_chunks(sop_dir)
    if not chunks:
        raise FileNotFoundError(f"No markdown docs found in {sop_dir}")
    vectors = embedder.embed_documents([c.content for c in chunks])
    docs = sorted({c.doc_name for c in chunks})
    session.execute(delete(DocChunk).where(DocChunk.doc_name.in_(docs)))
    session.add_all(
        DocChunk(doc_name=c.doc_name, title=c.title, section=c.section, chunk_index=c.index,
                 content=c.content, embedding=v)
        for c, v in zip(chunks, vectors, strict=True)
    )  # fmt: skip
    session.flush()
    counts: dict[str, int] = {}
    for c in chunks:
        counts[c.doc_name] = counts.get(c.doc_name, 0) + 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed SOP docs into pgvector")
    parser.add_argument("--sop-dir", type=Path, default=DEFAULT_SOP_DIR)
    args = parser.parse_args()
    from opspilot.db.session import session_scope

    t0 = time.perf_counter()
    with session_scope() as s:
        counts = ingest(s, args.sop_dir)
    print(f"Ingested {sum(counts.values())} chunks from {len(counts)} docs "
          f"in {time.perf_counter() - t0:.1f}s")  # fmt: skip
    for doc, n in counts.items():
        print(f"  {doc:<36} {n:>3} chunks")


if __name__ == "__main__":
    main()
