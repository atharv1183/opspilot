"""RAG schema: pgvector extension + rag.doc_chunks (kept outside the 14 ERP tables)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24 19:20:00
"""
import os
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS rag")
    op.create_table(
        "doc_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doc_name", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("section", sa.String(length=200), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doc_chunks")),
        schema="rag",
    )
    op.create_index(
        op.f("ix_rag_doc_chunks_doc_name"), "doc_chunks", ["doc_name"], unique=False, schema="rag"
    )
    # HNSW index for cosine-distance nearest-neighbour search.
    op.execute(
        "CREATE INDEX ix_rag_doc_chunks_embedding_hnsw ON rag.doc_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rag.ix_rag_doc_chunks_embedding_hnsw")
    op.drop_index(op.f("ix_rag_doc_chunks_doc_name"), table_name="doc_chunks", schema="rag")
    op.drop_table("doc_chunks", schema="rag")
    op.execute("DROP SCHEMA IF EXISTS rag")
