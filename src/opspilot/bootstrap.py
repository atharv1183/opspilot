"""Idempotent data bootstrap: legacy migration -> demo seed -> SOP embeddings.

Runs on API container start. Does nothing if data is already present unless ``--force``.

    python -m opspilot.bootstrap            # first start only
    python -m opspilot.bootstrap --force    # wipe and reload everything (make seed)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import func, select

from opspilot.db.models import DocChunk, Supplier
from opspilot.db.session import get_sessionmaker, session_scope

ROOT = Path(__file__).resolve().parents[2]


def bootstrap(force: bool = False) -> None:
    """Load ERP data and the RAG index if missing (or always with ``force``)."""
    sys.path.insert(0, str(ROOT))
    from migration.migrate import LEGACY_DIR, REPORT_PATH, render_report, run_migration

    with session_scope() as s:
        has_erp = (s.scalar(select(func.count()).select_from(Supplier)) or 0) > 0
        has_rag = (s.scalar(select(func.count()).select_from(DocChunk)) or 0) > 0

    if force or not has_erp:
        t0 = time.perf_counter()
        if not (LEGACY_DIR / "suppliers.csv").exists():
            from migration.generate_legacy import generate

            generate(LEGACY_DIR)
        session = get_sessionmaker()()
        try:
            result = run_migration(session, LEGACY_DIR)
        finally:
            session.close()
        REPORT_PATH.write_text(render_report(result), encoding="utf-8")
        print(f"[bootstrap] migrated {result.total_loaded:,}/{result.total_read:,} legacy rows")
        from opspilot.seed import run_seed

        with session_scope() as s:
            counts = run_seed(s)
        print(f"[bootstrap] seeded {counts} in {time.perf_counter() - t0:.1f}s")
    else:
        print("[bootstrap] ERP data present - skipping migration/seed")

    if force or not has_rag:
        from opspilot.rag.ingest import ingest

        with session_scope() as s:
            counts = ingest(s)
        print(f"[bootstrap] embedded {sum(counts.values())} chunks from {len(counts)} SOP docs")
    else:
        print("[bootstrap] RAG index present - skipping ingest")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load OpsPilot demo data")
    parser.add_argument("--force", action="store_true", help="reload everything")
    bootstrap(parser.parse_args().force)


if __name__ == "__main__":
    main()
