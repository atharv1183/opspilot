"""Migration pipeline: de-duplication, reconciliation and the loaded database."""

from datetime import date

from sqlalchemy import func, select

from migration.migrate import (
    LEGACY_DIR,
    EntityStats,
    dedupe_suppliers,
    render_report,
    run_migration,
)
from migration.schemas import SupplierRecord
from opspilot.db.models import ERP_TABLES, Item, PurchaseOrder, Supplier


def _sup(code, name, tax, created, email=None):
    return SupplierRecord(legacy_code=code, name=name, tax_id=tax, email=email, phone=None,
                          country=None, payment_terms="NET30", status="APPROVED", rating=None,
                          created=created)  # fmt: skip


def test_dedupe_suppliers_by_tax_id_and_name():
    stats = EntityStats("s", "t")
    recs = [
        _sup("V1", "Acme Corp", "11-1111111", date(2010, 1, 1)),
        _sup("V2", "ACME CORP", "11-1111111", date(2015, 1, 1), email="ap@acme.com"),  # same tax id
        _sup("V3", "Acme Corp.", None, date(2016, 1, 1)),  # no tax id, same name
        _sup("V4", "Acme Corp", "22-2222222", date(2017, 1, 1)),  # same name, other tax id
    ]
    groups, code_map = dedupe_suppliers(recs, stats, [])
    assert len(groups) == 2
    assert stats.merged == 2
    assert code_map["V1"] == code_map["V2"] == code_map["V3"] != code_map["V4"]
    survivor = groups[code_map["V1"]]
    assert survivor["legacy_codes"][0] == "V1"  # earliest record survives
    assert survivor["email"] == "ap@acme.com"  # back-filled from the duplicate


def test_dry_run_reconciles_every_file():
    result = run_migration(None, LEGACY_DIR, dry_run=True)
    assert result.total_read >= 5000
    assert result.total_loaded >= 5000
    for st in result.stats.values():
        assert st.reconciles(), st
    assert result.stats["suppliers"].merged == 30
    assert result.stats["inventory"].rejected["orphan item reference"] > 0
    assert result.stats["items"].rejected["unrecognised unit of measure"] == 10
    report = render_report(result)
    assert "Rows read" in report and "orphan supplier reference" in report


def test_loaded_database_matches_report(db, migration_result):
    counts = migration_result.entity_counts
    assert db.scalar(select(func.count()).select_from(Supplier)) == counts["suppliers"] == 280
    migrated_items = select(func.count()).select_from(Item).where(Item.sku.not_like("ITM-99%"))
    assert db.scalar(migrated_items) == counts["items"]  # API tests may add ITM-99xx
    migrated_pos = (
        select(func.count())
        .select_from(PurchaseOrder)
        .where(PurchaseOrder.requisition_id.is_(None))
    )
    assert db.scalar(migrated_pos) == counts["purchase_orders"]
    assert len(ERP_TABLES) == 14
    # duplicate vendor numbers are kept on the surviving supplier
    merged = db.scalar(select(Supplier).where(func.jsonb_array_length(Supplier.legacy_codes) > 1))
    assert merged is not None and len(merged.legacy_codes) >= 2
