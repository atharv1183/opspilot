"""Unit tests for the migration cleaning rules and pydantic row validation (no DB)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from migration import cleaning as c
from migration.schemas import (
    InventoryRecord,
    ItemRecord,
    POLineRecord,
    SupplierRecord,
    rejection_reason,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-03-05", date(2026, 3, 5)),
        ("03/05/2026", date(2026, 3, 5)),  # US slash format
        ("05.03.2026", date(2026, 3, 5)),  # EU dot format
        ("20260305", date(2026, 3, 5)),
        ("Mar 05, 2026", date(2026, 3, 5)),
        (" 2026-03-05 ", date(2026, 3, 5)),
    ],
)
def test_parse_date_mixed_formats(raw, expected):
    assert c.parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/31/2026", "2022-02-30", "yesterday", "", None])
def test_parse_date_rejects_garbage(raw):
    with pytest.raises(ValueError):
        c.parse_date(raw)
    assert c.parse_optional_date(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [("each", "EA"), ("PCS", "EA"), ("ea.", "EA"), ("carton", "BOX"), ("kgs", "KG"),
     ("Kilogram", "KG"), ("litre", "L"), ("MTR", "M")],
)  # fmt: skip
def test_normalize_uom(raw, expected):
    assert c.normalize_uom(raw) == expected


@pytest.mark.parametrize("raw", ["??", "bundle", "lot", "", "N/A"])
def test_bad_uom_rejected(raw):
    with pytest.raises(ValueError):
        c.normalize_uom(raw)


def test_numbers_and_quantities():
    assert c.parse_decimal("$1,234.50") == Decimal("1234.50")
    assert c.parse_quantity(" 1,200 ") == Decimal("1200")
    assert c.parse_quantity("12.0") == Decimal("12.0")
    with pytest.raises(ValueError, match="negative"):
        c.parse_quantity("-5")
    with pytest.raises(ValueError, match="missing"):
        c.parse_quantity("")
    with pytest.raises(ValueError, match="invalid"):
        c.parse_decimal("TBD")


def test_code_normalisation():
    assert c.normalize_item_no(" p-1001 ") == "ITM-1001"
    assert c.normalize_item_no("P1001") == "ITM-1001"
    assert c.normalize_doc_no("po 10001", "PO") == "PO-10001"
    assert c.normalize_doc_no("so-20001", "SO") == "SO-20001"
    for raw in ["Whse B", "wh_b", "B", "WAREHOUSE B", "WH-B"]:
        assert c.normalize_warehouse(raw) == "WH-B"
    with pytest.raises(ValueError):
        c.normalize_warehouse("Mars")


def test_master_data_fields():
    assert c.normalize_country("U.S.") == "United States"
    assert c.normalize_country("Deutschland") == "Germany"
    assert c.normalize_terms("Net 45") == "NET45"
    assert c.normalize_terms("30 days") == "NET30"
    assert c.normalize_email("Sales@Acme.COM") == "sales@acme.com"
    assert c.normalize_email("sales at acme.com") is None
    assert c.normalize_tax_id("123456789") == "12-3456789"
    assert c.normalize_tax_id("12-34") is None


def test_name_key_matches_duplicate_variants_only():
    base = c.name_key("Shaw, Price and White")
    for variant in ["SHAW, PRICE AND WHITE", "shaw, price and white.", "Shaw Price & White  "]:
        assert c.name_key(variant) == base
    assert c.name_key("Smith LLC") != c.name_key("Smith Inc")


def test_supplier_record_cleans_and_rejects():
    row = {"SUPP_NO": "V1", "SUPP_NAME": " Acme,  Inc. ", "TAX_ID": "123456789",
           "CONTACT_EMAIL": "x at y.com", "PHONE": "555", "COUNTRY": "USA", "TERMS": "Net 45",
           "STATUS": "A", "RATING": "4.2", "CREATED": "22.05.2017"}  # fmt: skip
    rec = SupplierRecord.from_row(row)
    assert (rec.name, rec.tax_id, rec.email, rec.status) == (
        "Acme, Inc",
        "12-3456789",
        None,
        "APPROVED",
    )
    with pytest.raises(ValidationError) as err:
        SupplierRecord.from_row({**row, "SUPP_NAME": ""})
    assert rejection_reason(err.value) == "missing name"


def test_item_inventory_and_po_line_records():
    item = ItemRecord.from_row({"ITEM_NO": "p-1001", "DESCRIPTION": "Hex Bolt M8", "CATEGORY": "Fasteners",
                                "UOM": "pcs", "STD_COST": "$1.20", "LIST_PRICE": "",
                                "LEAD_TIME": "7 days", "PREF_SUPPLIER": "V1001"})  # fmt: skip
    assert (item.sku, item.uom, item.standard_cost, item.list_price, item.lead_time_days) == (
        "ITM-1001",
        "EA",
        Decimal("1.20"),
        None,
        7,
    )
    with pytest.raises(ValidationError) as err:
        InventoryRecord.from_row({"ITEM_NO": "P-1", "WHSE": "A", "QTY_ON_HAND": "-3",
                                  "REORDER_PT": "1", "REORDER_QTY": "1", "LAST_COUNT": ""})  # fmt: skip
    assert "negative" in rejection_reason(err.value)
    line = POLineRecord.from_row({"PO_NO": "po 10001", "PO_DATE": "Oct 25, 2026", "SUPP_NO": "V1",
                                  "WHSE": "Whse B", "LINE_NO": "1", "ITEM_NO": " P1377 ",
                                  "QTY": "1,000", "UNIT_PRICE": "$1,331.92", "STATUS": "Open",
                                  "EXPECTED_DATE": ""})  # fmt: skip
    assert (line.doc_number, line.warehouse, line.quantity, line.status) == (
        "PO-10001",
        "WH-B",
        Decimal("1000"),
        "SENT",
    )
