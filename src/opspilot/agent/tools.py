"""The agent's 10 tools.

Each tool is a thin, typed wrapper around the ERP services (or the SOAP portal / RAG index) that
returns a JSON-serialisable dict with a human-readable ``summary``. Errors are returned as
``{"error": ..., "summary": ...}`` so the planner can recover instead of crashing.

Write tools (listed in ``WRITE_TOOLS``) are gated by a human-in-the-loop approval in the graph.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from functools import wraps
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from opspilot.config import get_settings
from opspilot.db.models import Item, Supplier
from opspilot.db.session import session_scope
from opspilot.erp import ERPError, o2c, p2p
from opspilot.erp import inventory as inv_svc
from opspilot.erp.lookup import resolve_supplier
from opspilot.rag.retriever import search, unique_docs
from opspilot.soap.client import PortalUnavailable, get_portal_client

WRITE_TOOLS = {"create_purchase_order"}


def _safe(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Convert business errors into a tool result the LLM can read."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except (ERPError, PortalUnavailable) as exc:
            return {"error": str(exc), "summary": f"Error: {exc}"}

    return wrapper


# ---------------------------------------------------------------------------- 1
@tool
@_safe
def get_inventory_level(item: str, warehouse: str | None = None) -> dict:
    """Get on-hand, reserved and available stock for an item, per warehouse.

    Args:
        item: SKU like 'ITM-1088' or an item name like 'Hydraulic Oil ISO32'.
        warehouse: optional warehouse ('WH-B', 'Warehouse B', 'B'). Omit for all warehouses.
    """
    with session_scope() as s:
        return inv_svc.inventory_level(s, item, warehouse)


# ---------------------------------------------------------------------------- 2
@tool
@_safe
def find_low_stock_items(warehouse: str | None = None) -> dict:
    """List items whose stock is at or below the reorder level (most critical first).

    Args:
        warehouse: optional warehouse ('WH-B', 'Warehouse B'). Omit for all warehouses.
    """
    with session_scope() as s:
        return inv_svc.low_stock_items(s, warehouse)


# ---------------------------------------------------------------------------- 3
@tool
@_safe
def create_purchase_requisition(item: str, qty: float, warehouse: str | None = None) -> dict:
    """Create a purchase requisition (internal request; no spend committed).

    Use this before creating a purchase order. Typically qty = the item's reorder quantity.

    Args:
        item: SKU or item name.
        qty: quantity to request (in the item's unit of measure).
        warehouse: ship-to warehouse; omit to use the warehouse most below reorder level.
    """
    with session_scope() as s:
        pr = p2p.create_requisition(s, item, qty, warehouse)
        item_obj = s.get(Item, pr.item_id)
        assert item_obj is not None
        wh = pr.warehouse.code
        return {
            "requisition_id": pr.pr_number, "item": item_obj.sku, "item_name": item_obj.name,
            "quantity": float(pr.quantity), "warehouse": wh, "status": str(pr.status),
            "needed_by": pr.needed_by.isoformat() if pr.needed_by else None,
            "summary": (f"Created requisition {pr.pr_number} for {float(pr.quantity):g} x "
                        f"{item_obj.sku} {item_obj.name} to {wh} (needed by {pr.needed_by})."),
        }  # fmt: skip


# ---------------------------------------------------------------------------- 4
@tool
@_safe
def create_purchase_order(requisition_id: str, supplier_id: str) -> dict:
    """Convert a purchase requisition into a purchase order with the chosen supplier.

    WRITE ACTION: commits spend; a human must approve before it runs. The supplier must be
    APPROVED. The unit price comes from the supplier portal quote. POs above the auto-approval
    limit are created as PENDING_APPROVAL for a manager.

    Args:
        requisition_id: e.g. 'PR-5001'.
        supplier_id: supplier code e.g. 'SUP-0123' (from get_supplier_quote).
    """
    with session_scope() as s:
        po = p2p.create_purchase_order(s, requisition_id, supplier_id)
        sup = s.get(Supplier, po.supplier_id)
        assert sup is not None
        line = po.lines[0]
        return {
            "po_number": po.po_number, "status": str(po.status), "supplier": sup.supplier_code,
            "supplier_name": sup.name, "total_amount": float(po.total_amount),
            "unit_price": float(line.unit_price), "quantity": float(line.quantity),
            "expected_date": po.expected_date.isoformat() if po.expected_date else None,
            "summary": (f"Created {po.po_number} for {requisition_id} with {sup.name} "
                        f"({sup.supplier_code}): {float(line.quantity):g} @ "
                        f"${float(line.unit_price):,.2f} = ${float(po.total_amount):,.2f}, "
                        f"status {po.status}, expected {po.expected_date}."),
        }  # fmt: skip


# ---------------------------------------------------------------------------- 5
@tool
@_safe
def get_po_status(po_id: str) -> dict:
    """Get the status of a purchase order: receipts, invoices and live delivery tracking.

    Delivery status comes from the legacy Supplier Portal (SOAP).

    Args:
        po_id: purchase order number, e.g. 'PO-10042'.
    """
    with session_scope() as s:
        result = p2p.po_status(s, po_id)
    try:
        d = get_portal_client().get_delivery_status(result["po_number"])
        result["delivery"] = d
        extra = f" Supplier portal: {d['status']}"
        if d.get("carrier"):
            extra += f" via {d['carrier']} ({d['tracking_number']}), ETA {d['eta']}"
        result["summary"] += extra + f" - {d['last_event']}"
    except (PortalUnavailable, ERPError) as exc:
        result["delivery"] = None
        result["summary"] += f" (Supplier portal unavailable: {exc})"
    return result


# ---------------------------------------------------------------------------- 6
@tool("three_way_match")
@_safe
def three_way_match_tool(invoice_id: str) -> dict:
    """Run the 3-way match (PO vs goods receipt vs supplier invoice) and explain mismatches.

    Use for questions like 'Why is invoice INV-1042 on hold?'.

    Args:
        invoice_id: supplier invoice number, e.g. 'INV-1042'.
    """
    with session_scope() as s:
        return p2p.three_way_match(s, invoice_id)


# ---------------------------------------------------------------------------- 7
@tool
@_safe
def get_sales_order_status(so_id: str) -> dict:
    """Get a sales order's status: lines shipped, shipments/tracking, invoices, credit hold.

    Args:
        so_id: sales order number, e.g. 'SO-20042'.
    """
    with session_scope() as s:
        return o2c.sales_order_status(s, so_id)


# ---------------------------------------------------------------------------- 8
@tool
@_safe
def check_available_to_promise(
    item: str, qty: float, date: str | None = None, warehouse: str | None = None
) -> dict:
    """Available-to-promise: can we deliver qty of an item by a date?

    ATP = on hand - reserved + open PO receipts expected by the date.

    Args:
        item: SKU or item name.
        qty: quantity the customer wants.
        date: promise date 'YYYY-MM-DD' (default: 14 days from today).
        warehouse: optional warehouse to ship from; omit for all warehouses.
    """
    d = _parse_date(date) if date else dt.date.today() + dt.timedelta(days=14)
    with session_scope() as s:
        return inv_svc.available_to_promise(s, item, qty, d, warehouse)


def _parse_date(value: str) -> dt.date:
    """ISO date, or a loose date like 'Dec 15 2026' / '15 December'."""
    from dateutil import parser as dparser

    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        try:
            return dparser.parse(value, default=dt.datetime.now()).date()
        except (ValueError, OverflowError) as exc:
            raise ERPError(f"Could not understand the date '{value}'. Use YYYY-MM-DD.") from exc


# ---------------------------------------------------------------------------- 9
@tool
@_safe
def get_supplier_quote(item: str, qty: float) -> dict:
    """Get live quotes for an item from the legacy Supplier Portal (SOAP via zeep).

    Returns all quotes (cheapest first) and the cheapest APPROVED supplier, which is the one
    procurement policy says to use.

    Args:
        item: SKU or item name.
        qty: quantity to quote.
    """
    with session_scope() as s:
        from opspilot.erp.lookup import resolve_item

        sku = resolve_item(s, item).sku
    quotes = get_portal_client().get_supplier_quote(sku, qty)
    approved = [q for q in quotes if q["supplier_status"] == "APPROVED"]
    best = approved[0] if approved else None
    excluded = [q for q in quotes if q["supplier_status"] != "APPROVED"]
    lines = "; ".join(
        f"{q['supplier_code']} {q['supplier_name']} ({q['supplier_status']}) "
        f"${q['unit_price']:,.2f}/unit, {q['lead_time_days']}d lead time"
        for q in quotes
    )
    if best:
        summary = (f"Quotes for {qty:g} x {sku}: {lines}. Cheapest approved supplier: "
                   f"{best['supplier_code']} {best['supplier_name']} at ${best['unit_price']:,.2f}"
                   f"/unit (total ${best['total']:,.2f}).")  # fmt: skip
    else:
        summary = f"Quotes for {qty:g} x {sku}: {lines}. No approved supplier offers this item."
    if excluded:
        summary += (
            " Not eligible (not APPROVED): " + ", ".join(q["supplier_code"] for q in excluded) + "."
        )
    return {"item": sku, "quantity": qty, "quotes": quotes, "cheapest_approved": best,
            "summary": summary}  # fmt: skip


# ---------------------------------------------------------------------------- 10
@tool
@_safe
def search_sop_docs(query: str) -> dict:
    """Search company SOP / policy documents (procurement, approval limits, 3-way match
    tolerance, returns, cycle counts, supplier onboarding, credit, ATP, goods receipt ...).

    Always cite the returned doc names in the answer, e.g. [approval-limits.md].

    Args:
        query: natural-language question about a policy or procedure.
    """
    with session_scope() as s:
        hits = search(s, query, get_settings().rag_top_k)
    docs = unique_docs(hits)
    top = hits[0] if hits else None
    summary = (f"Most relevant: [{top.doc_name}] {top.content[:700]}" if top
               else "No matching SOP documents.")  # fmt: skip
    return {
        "query": query,
        "results": [{"doc_name": h.doc_name, "section": h.section, "score": h.score,
                     "content": h.content} for h in hits],
        "sources": docs,
        "summary": summary,
    }  # fmt: skip


TOOLS: list[BaseTool] = [
    get_inventory_level,
    find_low_stock_items,
    create_purchase_requisition,
    create_purchase_order,
    get_po_status,
    three_way_match_tool,
    get_sales_order_status,
    check_available_to_promise,
    get_supplier_quote,
    search_sop_docs,
]
TOOL_BY_NAME: dict[str, BaseTool] = {t.name: t for t in TOOLS}


def preview_write_action(name: str, args: dict[str, Any]) -> str:
    """Human-readable description of a pending write action for the approval prompt."""
    if name != "create_purchase_order":
        return f"{name}({args})"
    try:
        with session_scope() as s:
            pr = p2p.resolve_requisition(s, args.get("requisition_id", ""))
            item = s.get(Item, pr.item_id)
            sup = resolve_supplier(s, args.get("supplier_id", ""))
            assert item is not None
            quote = next((q for q in p2p.supplier_quotes(s, item.sku, float(pr.quantity))
                          if q.supplier_code == sup.supplier_code), None)  # fmt: skip
            limit = get_settings().po_auto_approval_limit
            if quote is None:
                return (f"Create PO from {pr.pr_number}: {float(pr.quantity):g} x {item.sku} "
                        f"from {sup.supplier_code} {sup.name} (supplier does not quote this item)")  # fmt: skip
            route = ("auto-approved, within the $" f"{limit:,.0f} limit" if float(quote.total) <= limit
                     else f"above ${limit:,.0f}: will wait for manager approval")  # fmt: skip
            return (f"Create PO from {pr.pr_number}: {float(pr.quantity):g} x {item.sku} {item.name} "
                    f"from {sup.supplier_code} {sup.name} ({sup.status}) at "
                    f"${float(quote.unit_price):,.2f} = ${float(quote.total):,.2f} ({route})")  # fmt: skip
    except ERPError as exc:
        return f"create_purchase_order({args}) - note: {exc}"


def all_item_names() -> list[tuple[str, str]]:
    """(sku, name) pairs, used by the offline planner for entity extraction."""
    with session_scope() as s:
        return [(sku, name) for sku, name in s.execute(select(Item.sku, Item.name))]
