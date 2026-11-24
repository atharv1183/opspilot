"""Resolve the loose references people (and LLMs) use for items, warehouses and documents."""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from opspilot.db.models import Item, PurchaseOrder, SalesOrder, Supplier, SupplierInvoice, Warehouse
from opspilot.erp import ERPError

_SKU_RE = re.compile(r"^\s*(?:ITM|ITEM|P)?[\s#-]*(\d{3,6})\s*$", re.IGNORECASE)


def resolve_item(session: Session, ref: str) -> Item:
    """Find an item by SKU ('ITM-1042', '1042') or by (partial) name ('hex bolt m8')."""
    ref = (ref or "").strip()
    if not ref:
        raise ERPError("No item given.")
    m = _SKU_RE.match(ref)
    if m:
        item = session.scalar(select(Item).where(Item.sku == f"ITM-{int(m.group(1))}"))
        if item:
            return item
        raise ERPError(f"Item '{ref}' not found.")
    exact = session.scalars(select(Item).where(func.lower(Item.name) == ref.lower())).all()
    if len(exact) == 1:
        return exact[0]
    tokens = [t for t in re.split(r"\s+", ref) if t]
    stmt = select(Item).where(*[Item.name.ilike(f"%{t}%") for t in tokens]).order_by(Item.sku)
    matches = session.scalars(stmt.limit(6)).all()
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ERPError(f"No item matches '{ref}'.")
    options = ", ".join(f"{i.sku} ({i.name})" for i in matches[:5])
    raise ERPError(f"'{ref}' is ambiguous. Did you mean: {options}?")


def resolve_warehouse(session: Session, ref: str | None) -> Warehouse | None:
    """'Warehouse B', 'WH-B', 'b', 'Dallas' -> Warehouse. None/'' -> None (all warehouses)."""
    if ref is None or not str(ref).strip() or str(ref).strip().lower() in {"all", "any", "none"}:
        return None
    v = str(ref).strip()
    m = re.fullmatch(r"(?:wh|whse|warehouse)?[\s_-]*([a-z])", v, re.IGNORECASE)
    if m:
        wh = session.scalar(select(Warehouse).where(Warehouse.code == f"WH-{m.group(1).upper()}"))
        if wh:
            return wh
    wh = session.scalar(
        select(Warehouse).where(or_(Warehouse.name.ilike(v), Warehouse.city.ilike(f"%{v}%")))
    )
    if wh:
        return wh
    raise ERPError(f"Unknown warehouse '{ref}'. Valid: WH-A, WH-B, WH-C, WH-D.")


def _doc_no(ref: str | int, prefix: str) -> str:
    digits = re.sub(r"\D", "", str(ref))
    if not digits:
        raise ERPError(f"Invalid {prefix} reference '{ref}'.")
    return f"{prefix}-{int(digits)}"


def resolve_po(session: Session, ref: str | int) -> PurchaseOrder:
    """'PO-10042', '10042' or a numeric PO id."""
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == _doc_no(ref, "PO")))
    if po is None and str(ref).isdigit():
        po = session.get(PurchaseOrder, int(ref))
    if po is None:
        raise ERPError(f"Purchase order '{ref}' not found.")
    return po


def resolve_so(session: Session, ref: str | int) -> SalesOrder:
    """'SO-20042', '20042' or a numeric SO id."""
    so = session.scalar(select(SalesOrder).where(SalesOrder.so_number == _doc_no(ref, "SO")))
    if so is None and str(ref).isdigit():
        so = session.get(SalesOrder, int(ref))
    if so is None:
        raise ERPError(f"Sales order '{ref}' not found.")
    return so


def resolve_supplier_invoice(session: Session, ref: str | int) -> SupplierInvoice:
    """'INV-1042', '1042' or a numeric invoice id."""
    inv = session.scalar(
        select(SupplierInvoice).where(SupplierInvoice.invoice_number == _doc_no(ref, "INV"))
    )
    if inv is None:
        raise ERPError(f"Supplier invoice '{ref}' not found.")
    return inv


def resolve_supplier(session: Session, ref: str | int) -> Supplier:
    """'SUP-0042', a legacy vendor number ('V1042'), a numeric id or an exact name."""
    v = str(ref).strip()
    m = re.fullmatch(r"(?:SUP)?[\s-]*(\d{1,4})", v, re.IGNORECASE)
    if m:
        sup = session.scalar(
            select(Supplier).where(Supplier.supplier_code == f"SUP-{int(m.group(1)):04d}")
        )
        if sup:
            return sup
    sup = session.scalar(select(Supplier).where(Supplier.legacy_codes.contains([v.upper()])))
    if sup:
        return sup
    sup = session.scalar(select(Supplier).where(func.lower(Supplier.name) == v.lower()))
    if sup:
        return sup
    raise ERPError(f"Supplier '{ref}' not found.")
