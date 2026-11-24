"""Shared fixtures.

Tests run against a dedicated Postgres database (``TEST_DATABASE_URL``, default
``opspilot_test`` on localhost:5433) that is rebuilt once per session from the real legacy
migration + seed, so tool/API tests exercise realistic, deterministic data. Embeddings use the
download-free ``hash`` embedder and the agent uses the offline planner, so no API keys or model
downloads are needed. The SOAP Supplier Portal runs in-process on a random port.
"""

from __future__ import annotations

import os
import threading

# Configure BEFORE importing opspilot (settings are cached on first use).
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://opspilot:opspilot@localhost:5433/opspilot_test"
)
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["LLM_PROVIDER"] = "offline"

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from migration.migrate import LEGACY_DIR, run_migration  # noqa: E402
from opspilot.db.base import Base  # noqa: E402
from opspilot.db.session import get_engine, get_sessionmaker, session_scope  # noqa: E402


@pytest.fixture(scope="session")
def migration_result():
    """Fresh schema + full legacy migration + demo seed + SOP index (hash embeddings)."""
    from opspilot.db import models  # noqa: F401
    from opspilot.rag.embeddings import HashEmbedder
    from opspilot.rag.ingest import ingest
    from opspilot.seed import run_seed

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS rag"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = get_sessionmaker()()
    try:
        result = run_migration(session, LEGACY_DIR)
    finally:
        session.close()
    with session_scope() as s:
        run_seed(s)
        ingest(s, embedder=HashEmbedder(384))
    return result


@pytest.fixture(scope="session")
def soap_url(migration_result):
    """Run the spyne SOAP service in a background thread and point the zeep client at it."""
    from opspilot.config import get_settings
    from opspilot.soap.client import get_portal_client
    from opspilot.soap.service import make_soap_server

    server = make_soap_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/?wsdl"
    get_settings().soap_url = url
    get_portal_client.cache_clear()
    yield url
    server.shutdown()


@pytest.fixture
def db(migration_result):
    """A session for direct assertions (rolled back after the test)."""
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def api(soap_url):
    from fastapi.testclient import TestClient

    from opspilot.api.main import app

    return TestClient(app)
