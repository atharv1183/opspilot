"""Each of the 10 agent tools against the seeded test database (+ in-process SOAP service)."""

from opspilot.agent.tools import TOOL_BY_NAME, TOOLS, WRITE_TOOLS


def call(name, **args):
    return TOOL_BY_NAME[name].invoke(args)


def test_exactly_ten_tools_and_one_gated_write():
    assert len(TOOLS) == 10
    assert {"create_purchase_order"} == WRITE_TOOLS


def test_get_inventory_level_by_sku_and_name(migration_result):
    r = call("get_inventory_level", item="ITM-1088", warehouse="Warehouse B")
    assert r["item"]["sku"] == "ITM-1088"
    assert r["locations"][0]["warehouse"] == "WH-B"
    assert r["locations"][0]["below_reorder_level"] is True
    by_name = call("get_inventory_level", item="Hydraulic Oil ISO32")
    assert by_name["item"]["sku"] == "ITM-1088" and len(by_name["locations"]) >= 3


def test_get_inventory_level_unknown_item_returns_error(migration_result):
    r = call("get_inventory_level", item="ITM-99999")
    assert "error" in r and "not found" in r["error"]


def test_find_low_stock_items(migration_result):
    r = call("find_low_stock_items", warehouse="WH-B")
    assert r["count"] == 4
    assert {x["sku"] for x in r["items"]} >= {"ITM-1088", "ITM-1598"}
    assert all(x["qty_on_hand"] <= x["reorder_level"] for x in r["items"])
    assert call("find_low_stock_items")["count"] == 18  # 6 + 4 + 5 + 3 designed exceptions


def test_get_supplier_quote_uses_soap_and_picks_cheapest_approved(soap_url):
    r = call("get_supplier_quote", item="ITM-1088", qty=60)
    prices = [q["unit_price"] for q in r["quotes"]]
    assert prices == sorted(prices)
    assert r["cheapest_approved"]["supplier_status"] == "APPROVED"
    assert r["cheapest_approved"]["supplier_code"] == "SUP-0123"


def test_requisition_then_purchase_order(soap_url):
    pr = call("create_purchase_requisition", item="ITM-1088", qty=60, warehouse="WH-B")
    assert pr["requisition_id"].startswith("PR-") and pr["warehouse"] == "WH-B"
    po = call("create_purchase_order", requisition_id=pr["requisition_id"], supplier_id="SUP-0123")
    assert po["status"] == "APPROVED"  # $7,950.60 is within the $10k auto-approval limit
    assert po["total_amount"] == 7950.6
    again = call(
        "create_purchase_order", requisition_id=pr["requisition_id"], supplier_id="SUP-0123"
    )
    assert "already converted" in again["error"]


def test_purchase_order_above_limit_waits_for_manager(soap_url):
    pr = call("create_purchase_requisition", item="ITM-1578", qty=300, warehouse="WH-B")
    quote = call("get_supplier_quote", item="ITM-1578", qty=300)["cheapest_approved"]
    po = call("create_purchase_order", requisition_id=pr["requisition_id"],
              supplier_id=quote["supplier_code"])  # fmt: skip
    assert po["status"] == "PENDING_APPROVAL" and po["total_amount"] > 10_000


def test_purchase_order_rejects_non_approved_supplier(db, soap_url):
    from sqlalchemy import select

    from opspilot.db.enums import SupplierStatus
    from opspilot.db.models import Supplier

    blocked = db.scalar(select(Supplier).where(Supplier.status == SupplierStatus.BLOCKED))
    pr = call("create_purchase_requisition", item="ITM-1598", qty=40, warehouse="WH-B")
    r = call("create_purchase_order", requisition_id=pr["requisition_id"],
             supplier_id=blocked.supplier_code)  # fmt: skip
    assert "APPROVED" in r["error"]


def test_get_po_status_includes_soap_delivery(soap_url):
    r = call("get_po_status", po_id="PO-10005")
    assert r["status"] == "RECEIVED"
    assert r["delivery"]["status"] == "DELIVERED"
    assert "INV-1102" in r["summary"]


def test_three_way_match_tool(migration_result):
    r = call("three_way_match", invoice_id="INV-1042")
    assert r["match_status"] == "PRICE_VARIANCE" and r["invoice_status"] == "ON_HOLD"
    assert "+6.5%" in r["explanation"]


def test_get_sales_order_status(migration_result):
    r = call("get_sales_order_status", so_id="SO-20003")
    assert r["status"] == "INVOICED"
    assert r["shipments"] and r["invoices"][0]["status"] == "OVERDUE"


def test_check_available_to_promise(migration_result):
    ok = call("check_available_to_promise", item="ITM-1088", qty=50, date="2026-12-31")
    assert ok["can_promise"] is True
    no = call("check_available_to_promise", item="ITM-1088", qty=100000, date="2026-12-31")
    assert no["can_promise"] is False and no["summary"].startswith("No")


def test_search_sop_docs_cites_sources(migration_result):
    r = call("search_sop_docs", query="restocking fee for customer returns RMA")
    assert r["sources"][0] == "returns-policy.md"
    assert r["results"][0]["content"]
