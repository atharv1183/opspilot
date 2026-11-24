"""Inventory queries: stock levels, reorder exceptions and available-to-promise (ATP)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from opspilot.db.enums import POStatus
from opspilot.db.models import Inventory, Item, POLine, PurchaseOrder, Warehouse
from opspilot.erp.lookup import resolve_item, resolve_warehouse

OPEN_PO_STATUSES = (POStatus.APPROVED, POStatus.SENT, POStatus.PARTIALLY_RECEIVED)


def _num(v: Decimal | float | int | None) -> float:
    return float(v or 0)


def on_order_qty(session: Session, item_id: int, warehouse_id: int | None = None) -> Decimal:
    """Open PO quantity not yet received for an item (optionally in one warehouse)."""
    stmt = (
        select(func.coalesce(func.sum(POLine.quantity - POLine.qty_received), 0))
        .join(PurchaseOrder)
        .where(POLine.item_id == item_id, PurchaseOrder.status.in_(OPEN_PO_STATUSES))
    )
    if warehouse_id is not None:
        stmt = stmt.where(PurchaseOrder.warehouse_id == warehouse_id)
    return Decimal(session.scalar(stmt) or 0)


def inventory_level(session: Session, item_ref: str, warehouse_ref: str | None = None) -> dict:
    """Stock position of one item, per warehouse (or a single warehouse)."""
    item = resolve_item(session, item_ref)
    wh = resolve_warehouse(session, warehouse_ref)
    stmt = (
        select(Inventory)
        .options(joinedload(Inventory.warehouse))
        .where(Inventory.item_id == item.id)
        .join(Warehouse)
        .order_by(Warehouse.code)
    )
    if wh is not None:
        stmt = stmt.where(Inventory.warehouse_id == wh.id)
    rows = session.scalars(stmt).all()
    locations = []
    for r in rows:
        locations.append({
            "warehouse": r.warehouse.code,
            "warehouse_name": r.warehouse.name,
            "qty_on_hand": _num(r.qty_on_hand),
            "qty_reserved": _num(r.qty_reserved),
            "qty_available": _num(r.qty_on_hand - r.qty_reserved),
            "reorder_level": _num(r.reorder_level),
            "below_reorder_level": r.qty_on_hand <= r.reorder_level,
            "on_order": _num(on_order_qty(session, item.id, r.warehouse_id)),
            "last_counted_at": r.last_counted_at.isoformat() if r.last_counted_at else None,
        })  # fmt: skip
    total = sum(loc["qty_on_hand"] for loc in locations)
    where = wh.code if wh else "all warehouses"
    if not locations:
        summary = f"{item.sku} {item.name} is not stocked in {where}."
    else:
        parts = "; ".join(
            f"{loc['warehouse']}: {loc['qty_on_hand']:g} on hand ({loc['qty_available']:g} "
            f"available, reorder level {loc['reorder_level']:g})"
            for loc in locations
        )
        summary = f"{item.sku} {item.name} ({item.uom}) - {parts}. Total on hand: {total:g}."
    return {
        "item": {"sku": item.sku, "name": item.name, "uom": str(item.uom)},
        "warehouse": wh.code if wh else None,
        "locations": locations,
        "total_on_hand": total,
        "summary": summary,
    }


def low_stock_items(session: Session, warehouse_ref: str | None = None, limit: int = 50) -> dict:
    """Inventory rows at or below their reorder level, most critical first."""
    wh = resolve_warehouse(session, warehouse_ref)
    stmt = (
        select(Inventory)
        .options(joinedload(Inventory.item).joinedload(Item.preferred_supplier),
                 joinedload(Inventory.warehouse))
        .where(Inventory.qty_on_hand <= Inventory.reorder_level)
    )  # fmt: skip
    if wh is not None:
        stmt = stmt.where(Inventory.warehouse_id == wh.id)
    rows = session.scalars(stmt).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        ratio = _num(r.qty_on_hand) / _num(r.reorder_level) if r.reorder_level else 0.0
        out.append({
            "sku": r.item.sku,
            "name": r.item.name,
            "warehouse": r.warehouse.code,
            "qty_on_hand": _num(r.qty_on_hand),
            "reorder_level": _num(r.reorder_level),
            "reorder_qty": _num(r.reorder_qty),
            "shortfall": _num(r.reorder_level - r.qty_on_hand),
            "on_order": _num(on_order_qty(session, r.item_id, r.warehouse_id)),
            "coverage_pct": round(ratio * 100, 1),
            "preferred_supplier": r.item.preferred_supplier.supplier_code
            if r.item.preferred_supplier else None,
        })  # fmt: skip
    out.sort(key=lambda x: (x["coverage_pct"], x["sku"]))
    where = wh.code if wh else "all warehouses"
    if out:
        lst = "; ".join(
            f"{x['sku']} {x['name']} in {x['warehouse']} ({x['qty_on_hand']:g} on hand vs reorder "
            f"level {x['reorder_level']:g}, suggested reorder qty {x['reorder_qty']:g})"
            for x in out[:limit]
        )
        summary = f"{len(out)} item(s) at or below reorder level in {where}: {lst}."
    else:
        summary = f"No items are at or below reorder level in {where}."
    return {"warehouse": wh.code if wh else None, "count": len(out), "items": out[:limit],
            "summary": summary}  # fmt: skip


def available_to_promise(
    session: Session, item_ref: str, qty: float, by_date: date, warehouse_ref: str | None = None
) -> dict:
    """ATP = on hand - reserved + open PO receipts expected on or before ``by_date``.

    If the quantity cannot be promised by ``by_date`` the earliest date covered by scheduled
    receipts is returned, or the date implied by the item lead time if a new PO is needed.
    """
    item = resolve_item(session, item_ref)
    wh = resolve_warehouse(session, warehouse_ref)
    inv_stmt = select(Inventory).where(Inventory.item_id == item.id)
    if wh is not None:
        inv_stmt = inv_stmt.where(Inventory.warehouse_id == wh.id)
    invs = session.scalars(inv_stmt).all()
    available_now = sum((r.qty_on_hand - r.qty_reserved for r in invs), Decimal(0))

    rec_stmt = (
        select(PurchaseOrder.po_number, PurchaseOrder.expected_date,
               (POLine.quantity - POLine.qty_received).label("open_qty"))
        .join(POLine)
        .where(POLine.item_id == item.id, PurchaseOrder.status.in_(OPEN_PO_STATUSES),
               POLine.quantity > POLine.qty_received)
        .order_by(PurchaseOrder.expected_date)
    )  # fmt: skip
    if wh is not None:
        rec_stmt = rec_stmt.where(PurchaseOrder.warehouse_id == wh.id)
    receipts = [
        {"po_number": po, "expected_date": d.isoformat() if d else None, "qty": _num(q)}
        for po, d, q in session.execute(rec_stmt).all()
    ]
    by_date_supply = sum(
        Decimal(str(r["qty"])) for r in receipts
        if r["expected_date"] and date.fromisoformat(r["expected_date"]) <= by_date
    )  # fmt: skip
    atp = available_now + by_date_supply
    need = Decimal(str(qty))
    can_promise = atp >= need
    earliest: str | None = by_date.isoformat() if can_promise else None
    if not can_promise:
        running = available_now
        for r in receipts:
            running += Decimal(str(r["qty"]))
            if running >= need and r["expected_date"]:
                earliest = r["expected_date"]
                break
    where = wh.code if wh else "all warehouses"
    if can_promise:
        summary = (f"Yes - {qty:g} x {item.sku} {item.name} can be promised by {by_date} from "
                   f"{where}: available now {_num(available_now):g}, scheduled receipts by then "
                   f"{_num(by_date_supply):g}, ATP {_num(atp):g}.")  # fmt: skip
    else:
        tail = (f"Earliest date covered by open POs: {earliest}." if earliest else
                f"Open POs do not cover it; a new PO is needed (lead time {item.lead_time_days} "
                "days).")  # fmt: skip
        summary = (f"No - only {_num(atp):g} of {qty:g} x {item.sku} {item.name} can be promised "
                   f"by {by_date} from {where} (available now {_num(available_now):g}, scheduled "
                   f"receipts {_num(by_date_supply):g}). {tail}")  # fmt: skip
    return {
        "item": {"sku": item.sku, "name": item.name},
        "warehouse": wh.code if wh else None,
        "requested_qty": float(qty),
        "requested_date": by_date.isoformat(),
        "available_now": _num(available_now),
        "scheduled_receipts_by_date": _num(by_date_supply),
        "atp_qty": _num(atp),
        "can_promise": can_promise,
        "earliest_date": earliest,
        "open_po_receipts": receipts,
        "summary": summary,
    }
