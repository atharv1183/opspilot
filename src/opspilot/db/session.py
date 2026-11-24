"""Engine / session factory helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from opspilot.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    """Create (once per URL) the SQLAlchemy engine for ``url`` or the configured DATABASE_URL."""
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    """Return a session factory bound to the engine."""
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session (callers commit explicitly)."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
