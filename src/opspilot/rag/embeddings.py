"""Embedding back-ends: local sentence-transformers (default), OpenAI, or a hashing stub for tests."""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from opspilot.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """sentence-transformers model (default all-MiniLM-L6-v2, 384 dims), L2-normalised."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, device="cpu")
        get_dim = (
            getattr(self.model, "get_embedding_dimension", None)
            or self.model.get_sentence_embedding_dimension
        )
        self.dim = int(get_dim())

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class OpenAIEmbedder:
    """OpenAI text-embedding-3-small (1536 dims). Requires OPENAI_API_KEY and EMBEDDING_DIM=1536."""

    def __init__(self, model_name: str = "text-embedding-3-small") -> None:
        from langchain_openai import OpenAIEmbeddings

        s = get_settings()
        self._emb = OpenAIEmbeddings(model=model_name, api_key=s.openai_api_key or None)
        self.dim = 1536

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._emb.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._emb.embed_query(text)


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. No downloads; used in unit tests/CI."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)  # noqa: S324 - not security
            v[h % self.dim] += 1.0 if (h >> 8) % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@lru_cache
def get_embedder() -> Embedder:
    """Embedder selected by EMBEDDING_PROVIDER (local | openai | hash)."""
    s = get_settings()
    provider = s.embedding_provider.lower()
    if provider == "openai":
        return OpenAIEmbedder()
    if provider == "hash":
        return HashEmbedder(s.embedding_dim)
    return LocalEmbedder(s.embedding_model)
