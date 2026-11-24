"""Application settings loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration. Every field can be overridden by an env var."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://opspilot:opspilot@localhost:5433/opspilot"
    soap_url: str = "http://localhost:8001/?wsdl"

    llm_provider: str = "auto"  # auto | openai | gemini | offline
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    llm_temperature: float = 0.0

    embedding_provider: str = "local"  # local | openai | hash (tests only)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    rag_top_k: int = 4

    po_auto_approval_limit: float = 10_000.0
    three_way_price_tolerance_pct: float = 2.0
    three_way_qty_tolerance_pct: float = 0.0

    def resolved_llm_provider(self) -> str:
        """Resolve ``auto`` to a concrete provider based on which keys are configured."""
        provider = self.llm_provider.lower()
        if provider != "auto":
            return provider
        if self.openai_api_key:
            return "openai"
        if self.google_api_key:
            return "gemini"
        return "offline"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
