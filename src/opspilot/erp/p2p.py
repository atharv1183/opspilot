"""Procure-to-Pay: quotes, requisitions, purchase orders, PO status and 3-way match."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session, joinedload

from opspilot.config import get_settings
from opspilot.db.enums import (
    MatchStatus,
    POStatus,
    RequisitionStatus,
    SupplierInvoiceStatus,
    SupplierStatus,
)
from opspilot.db.models import (
    GoodsReceipt,
    Inventory,
    Item,
    POLine,
    PurchaseOrder,
    PurchaseRequisition,
    Supplier,
    SupplierInvoice,
)
from opspilot.erp import ERPError
from opspilot.erp.lookup import (
    resolve_item,
    resolve_po,
    resolve_supplier,
    resolve_supplier_invoice,
    resolve_warehouse,
)
from opspilot.erp.pricing import (
    Quote,
    catalog_supplier_codes,
    quote_lead_time,
    quote_unit_price,
)


def _money(v: Decimal | float) -> str:
    return f"${float(v):,.2f}"


# --------------------------------------------------------------------------- quotes
def supplier_quotes(session: Session, item_ref: str, qty: float) -> list[Quote]:
    """Quotes from every supplier that lists the item on the portal, cheapest first."""
    item = resolve_item(session, item_ref)
    q = Decimal(str(qty))
    if q <= 0:
        raise ERPError("Quantity must be positive.")
    all_codes = list(session.scalars(select(Supplier.supplier_code)))
    pref = item.preferred_supplier.supplier_code if item.preferred_supplier else None
    codes = catalog_supplier_codes(item.sku, all_codes, pref)
    sups = {s.supplier_code: s for s in session.scalars(
        select(Supplier).where(Supplier.supplier_code.in_(codes)))}  # fmt: skip
    quotes = []
    for code in codes:
        s = sups[code]
        price = quote_unit_price(item.standard_cost, code, item.sku, q)
        quotes.append(Quote(
            supplier_code=code, supplier_name=s.name, supplier_status=str(s.status),
            item_sku=item.sku, quantity=q, unit_price=price, currency="USD",
            lead_time_days=quote_lead_time(item.lead_time_days, code, item.sku),
            total=(price * q).quantize(Decimal("0.01")),
        ))  # fmt: skip
    return sorted(quotes, key=lambda x: (x.unit_price, x.supplier_code))


# --------------------------------------------------------------------------- requisitions
def _next_number(session: Session, column: Any, prefix: str, start: int) -> str:
    """Next document number such as PO-10374 (max existing + 1, or ``start``)."""
    max_no = session.scalar(
        select(func.max(cast(func.split_part(column, "-", 2), Integer))).where(
            column.like(f"{prefix}-%")
        )
    )
    return f"{prefix}-{(max_no or start - 1) + 1}"


def create_requisition(
    session: Session,
    item_ref: str,
    qty: float,
    warehouse_ref: str | None = None,
    requested_by: str = "opspilot-agent",
) -> PurchaseRequisition:
    """Create a purchase requisition (internal request, no spend committed yet).

    If no warehouse is given, the warehouse where the item is furthest below its reorder
    level is used (falls back to the first stocking warehouse).
    """
    item = resolve_item(session, item_ref)
    if qty <= 0:
        raise ERPError("Requisition quantity must be positive.")
    wh = resolve_warehouse(session, warehouse_ref)
    if wh is None:
        inv = session.scalars(
            select(Inventory).where(Inventory.item_id == item.id)
            .order_by((Inventory.qty_on_hand - Inventory.reorder_level), Inventory.warehouse_id)
        ).first()  # fmt: skip
        if inv is None:
            raise ERPError(f"{item.sku} is not stocked anywhere; specify a warehouse.")
        wh_id = inv.warehouse_id
    else:
        wh_id = wh.id
    pr = PurchaseRequisition(
        pr_number=_next_number(session, PurchaseRequisition.pr_number, "PR", 5001),
        item_id=item.id, warehouse_id=wh_id, quantity=Decimal(str(qty)),
        requested_by=requested_by, status=RequisitionStatus.SUBMITTED,
        needed_by=date.today() + timedelta(days=item.lead_time_days),
    )  # fmt: skip
    session.add(pr)
    session.flush()
    return pr


def resolve_requisition(session: Session, ref: str | int) -> PurchaseRequisition:
    """'PR-5001', '5001' or a numeric requisition id."""
    s = str(ref).strip().upper()
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        raise ERPError(f"Invalid requisition reference '{ref}'.")
    pr = session.scalar(
        select(PurchaseRequisition).where(PurchaseRequisition.pr_number == f"PR-{int(digits)}")
    )
    if pr is None:
        pr = session.get(PurchaseRequisition, int(digits))
    if pr is None:
        raise ERPError(f"Requisition '{ref}' not found.")
    return pr


# --------------------------------------------------------------------------- purchase orders
def create_purchase_order(
    session: Session,
    requisition_ref: str | int,
    supplier_ref: str | int,
    created_by: str = "opspilot-agent",
) -> PurchaseOrder:
    """Convert a requisition into a PO with the given supplier at the portal quote price.

    Business rules (see docs/sop/approval-limits.md and procurement-policy.md):
    * the supplier must be APPROVED and must offer the item;
    * a requisition can only be converted once;
    * POs up to PO_AUTO_APPROVAL_LIMIT are auto-approved, larger ones wait for a manager.
    """
    pr = resolve_requisition(session, requisition_ref)
    if pr.status == RequisitionStatus.CONVERTED:
        raise ERPError(f"{pr.pr_number} was already converted to a purchase order.")
    if pr.status == RequisitionStatus.REJECTED:
        raise ERPError(f"{pr.pr_number} was rejected and cannot be converted.")
    sup = resolve_supplier(session, supplier_ref)
    if sup.status != SupplierStatus.APPROVED:
        raise ERPError(
            f"Supplier {sup.supplier_code} ({sup.name}) is {sup.status}; POs may only be issued "
            "to APPROVED suppliers."
        )
    item = session.get(Item, pr.item_id)
    assert item is not None
    quote = next((q for q in supplier_quotes(session, item.sku, float(pr.quantity))
                  if q.supplier_code == sup.supplier_code), None)  # fmt: skip
    if quote is None:
        raise ERPError(f"Supplier {sup.supplier_code} does not offer {item.sku} on the portal.")
    limit = Decimal(str(get_settings().po_auto_approval_limit))
    total = quote.total
    auto = total <= limit
    po = PurchaseOrder(
        po_number=_next_number(session, PurchaseOrder.po_number, "PO", 10001),
        supplier_id=sup.id, requisition_id=pr.id, warehouse_id=pr.warehouse_id,
        status=POStatus.APPROVED if auto else POStatus.PENDING_APPROVAL,
        order_date=date.today(), expected_date=date.today() + timedelta(days=quote.lead_time_days),
        total_amount=total, created_by=created_by,
        approved_by="auto-approved (within limit)" if auto else None,
        lines=[POLine(line_no=1, item_id=item.id, quantity=pr.quantity,
                      unit_price=quote.unit_price)],
    )  # fmt: skip
    pr.status = RequisitionStatus.CONVERTED
    session.add(po)
    session.flush()
    return po


def po_status(session: Session, po_ref: str | int) -> dict:
    """PO header, lines with received quantities, receipts and linked invoices."""
    po = resolve_po(session, po_ref)
    po = session.scalar(
        select(PurchaseOrder)
        .options(joinedload(PurchaseOrder.supplier), joinedload(PurchaseOrder.warehouse),
                 joinedload(PurchaseOrder.lines).joinedload(POLine.item))
        .where(PurchaseOrder.id == po.id)
    )  # fmt: skip
    assert po is not None
    grns = session.scalars(select(GoodsReceipt).where(GoodsReceipt.po_id == po.id)
                           .order_by(GoodsReceipt.received_at)).all()  # fmt: skip
    invs = session.scalars(select(SupplierInvoice).where(SupplierInvoice.po_id == po.id)).all()
    lines = [{
        "line_no": ln.line_no, "sku": ln.item.sku, "name": ln.item.name,
        "quantity": float(ln.quantity), "unit_price": float(ln.unit_price),
        "qty_received": float(ln.qty_received),
        "qty_open": float(ln.quantity - ln.qty_received),
    } for ln in po.lines]  # fmt: skip
    ordered = sum(x["quantity"] for x in lines)
    received = sum(x["qty_received"] for x in lines)
    inv_txt = ", ".join(f"{i.invoice_number} ({i.status}, match {i.match_status})" for i in invs)
    summary = (
        f"{po.po_number} to {po.supplier.name} ({po.supplier.supplier_code}) for "
        f"{_money(po.total_amount)} is {po.status}. Ordered {po.order_date}, expected "
        f"{po.expected_date}, ship-to {po.warehouse.code}. Received {received:g} of {ordered:g} "
        f"units across {len(lines)} line(s) in {len({g.grn_number for g in grns})} GRN(s)."
        + (f" Invoices: {inv_txt}." if invs else " No supplier invoice yet.")
    )
    return {
        "po_number": po.po_number, "status": str(po.status),
        "supplier": {"code": po.supplier.supplier_code, "name": po.supplier.name},
        "warehouse": po.warehouse.code, "order_date": po.order_date.isoformat(),
        "expected_date": po.expected_date.isoformat() if po.expected_date else None,
        "total_amount": float(po.total_amount), "approved_by": po.approved_by,
        "lines": lines,
        "goods_receipts": [{"grn_number": g.grn_number, "po_line_id": g.po_line_id,
                            "qty_received": float(g.qty_received),
                            "qty_rejected": float(g.qty_rejected),
                            "received_at": g.received_at.date().isoformat()} for g in grns],
        "invoices": [{"invoice_number": i.invoice_number, "status": str(i.status),
                      "match_status": str(i.match_status), "total": float(i.total_amount)}
                     for i in invs],
        "summary": summary,
    }  # fmt: skip


# --------------------------------------------------------------------------- 3-way match
@dataclass
class LineMatch:
    """Result of matching one invoiced line against its PO line and goods receipts."""

    po_line_id: int
    line_no: int
    sku: str
    po_qty: float
    po_price: float
    received_qty: float
    invoiced_qty: float
    invoiced_price: float
    price_variance_pct: float
    issues: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    status: MatchStatus
    lines: list[LineMatch]
    explanation: str
    recommended_action: str


def evaluate_three_way_match(
    po_lines: list[dict[str, Any]],
    accepted_receipts: dict[int, float],
    invoice_lines: list[dict[str, Any]],
    price_tolerance_pct: float = 2.0,
    qty_tolerance_pct: float = 0.0,
) -> MatchResult:
    """Pure 3-way match: PO (what we agreed) vs GRN (what arrived) vs invoice (what is billed).

    ``po_lines``: [{id, line_no, sku, quantity, unit_price}]
    ``accepted_receipts``: {po_line_id: received - rejected}
    ``invoice_lines``: [{po_line_id, quantity, unit_price}]

    Status precedence: NO_RECEIPT > QTY_VARIANCE > PRICE_VARIANCE > MATCHED.
    Only *adverse* price variances (billed above PO price beyond tolerance) block payment.
    """
    by_id = {pl["id"]: pl for pl in po_lines}
    results: list[LineMatch] = []
    flags: set[MatchStatus] = set()
    for il in invoice_lines:
        pl = by_id.get(il["po_line_id"])
        if pl is None:
            raise ERPError(f"Invoice references unknown PO line {il['po_line_id']}.")
        po_price, inv_price = float(pl["unit_price"]), float(il["unit_price"])
        rec = float(accepted_receipts.get(pl["id"], 0.0))
        inv_qty = float(il["quantity"])
        var = round((inv_price - po_price) / po_price * 100, 2) if po_price else 0.0
        lm = LineMatch(pl["id"], pl["line_no"], pl["sku"], float(pl["quantity"]), po_price, rec,
                       inv_qty, inv_price, var)  # fmt: skip
        if rec <= 0:
            lm.issues.append(f"line {pl['line_no']} ({pl['sku']}): billed {inv_qty:g} but nothing "
                             "has been received (no GRN)")  # fmt: skip
            flags.add(MatchStatus.NO_RECEIPT)
        elif inv_qty > rec * (1 + qty_tolerance_pct / 100) + 1e-9:
            lm.issues.append(f"line {pl['line_no']} ({pl['sku']}): billed qty {inv_qty:g} exceeds "
                             f"received qty {rec:g} (PO qty {float(pl['quantity']):g})")  # fmt: skip
            flags.add(MatchStatus.QTY_VARIANCE)
        if var > price_tolerance_pct:
            lm.issues.append(
                f"line {pl['line_no']} ({pl['sku']}): billed unit price {_money(inv_price)} vs PO "
                f"price {_money(po_price)} (+{var:.1f}%, tolerance {price_tolerance_pct:g}%)"
            )
            flags.add(MatchStatus.PRICE_VARIANCE)
        results.append(lm)

    for status in (MatchStatus.NO_RECEIPT, MatchStatus.QTY_VARIANCE, MatchStatus.PRICE_VARIANCE):
        if status in flags:
            break
    else:
        status = MatchStatus.MATCHED
    issues = [i for lm in results for i in lm.issues]
    actions = {
        MatchStatus.MATCHED: "Release for payment per supplier terms.",
        MatchStatus.PRICE_VARIANCE: "Hold payment; ask the buyer to confirm the price change or "
        "request a credit note from the supplier for the difference.",
        MatchStatus.QTY_VARIANCE: "Hold payment; pay only for received quantity and request a "
        "corrected invoice or credit note, or post the missing goods receipt if goods arrived.",
        MatchStatus.NO_RECEIPT: "Hold payment until the warehouse posts a goods receipt (GRN) "
        "for the invoiced items.",
    }  # fmt: skip
    if status == MatchStatus.MATCHED:
        explanation = (f"All {len(results)} invoiced line(s) match the PO and goods receipts within "
                       f"tolerance ({price_tolerance_pct:g}% price).")  # fmt: skip
    else:
        explanation = "Mismatch: " + "; ".join(issues) + "."
    return MatchResult(status, results, explanation, actions[status])


def three_way_match(session: Session, invoice_ref: str | int, *, persist: bool = False) -> dict:
    """Run the 3-way match for a supplier invoice and explain any discrepancy."""
    inv = resolve_supplier_invoice(session, invoice_ref)
    po = session.scalar(
        select(PurchaseOrder)
        .options(joinedload(PurchaseOrder.lines).joinedload(POLine.item),
                 joinedload(PurchaseOrder.supplier))
        .where(PurchaseOrder.id == inv.po_id)
    )  # fmt: skip
    assert po is not None
    receipts: dict[int, float] = {}
    for g in session.scalars(select(GoodsReceipt).where(GoodsReceipt.po_id == po.id)):
        receipts[g.po_line_id] = receipts.get(g.po_line_id, 0.0) + float(
            g.qty_received - g.qty_rejected
        )
    po_lines = [
        {
            "id": ln.id,
            "line_no": ln.line_no,
            "sku": ln.item.sku,
            "quantity": ln.quantity,
            "unit_price": ln.unit_price,
        }
        for ln in po.lines
    ]
    s = get_settings()
    res = evaluate_three_way_match(
        po_lines,
        receipts,
        inv.lines,
        s.three_way_price_tolerance_pct,
        s.three_way_qty_tolerance_pct,
    )
    if persist:
        inv.match_status = res.status
        if res.status != MatchStatus.MATCHED:
            inv.status = SupplierInvoiceStatus.ON_HOLD
            inv.hold_reason = res.explanation
    po_value = sum(lm.invoiced_qty * lm.po_price for lm in res.lines)
    summary = (
        f"{inv.invoice_number} from {po.supplier.name} for {_money(inv.total_amount)} against "
        f"{po.po_number}: invoice status {inv.status}, 3-way match {res.status}. "
        f"{res.explanation} Recommended action: {res.recommended_action}"
    )
    return {
        "invoice_number": inv.invoice_number,
        "po_number": po.po_number,
        "supplier": po.supplier.name,
        "invoice_status": str(inv.status),
        "hold_reason": inv.hold_reason,
        "match_status": str(res.status),
        "invoice_subtotal": float(inv.subtotal),
        "invoice_total": float(inv.total_amount),
        "po_value_of_invoiced_lines": round(po_value, 2),
        "lines": [asdict(lm) for lm in res.lines],
        "explanation": res.explanation,
        "recommended_action": res.recommended_action,
        "summary": summary,
    }
