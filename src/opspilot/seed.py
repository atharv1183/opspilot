"""Seed downstream transactional data on top of the migrated legacy master data + orders.

The legacy export only contained POs and SOs. This step derives what a live ERP would also
hold, deterministically (fixed seed), so demos, tests and evals are reproducible:

* goods receipts (GRNs) for received / closed / partially received POs
* supplier invoices INV-1001... incl. designed 3-way-match exceptions, e.g. **INV-1042** is on
  hold for a price variance
* shipments + customer invoices for shipped / invoiced / closed sales orders
* inventory reservations for allocated sales orders and credit-hold exposure

Run: ``python -m opspilot.seed``
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, selectinload

from opspilot.db.enums import (
    CustomerInvoiceStatus,
    MatchStatus,
    POStatus,
    ShipmentStatus,
    SOStatus,
    SupplierInvoiceStatus,
)
from opspilot.db.models import (
    Customer,
    CustomerInvoice,
    GoodsReceipt,
    Inventory,
    POLine,
    PurchaseOrder,
    SalesOrder,
    Shipment,
    SOLine,
    SupplierInvoice,
)
from opspilot.erp.o2c import customer_open_ar
from opspilot.erp.p2p import three_way_match

SEED = 7
AS_OF = date(2026, 11, 24)  # business "today" used for seeded aging (overdue etc.)
CARRIERS = ["UPS", "FedEx", "DHL", "XPO Logistics"]
TAX_RATE = Decimal("0.08")

# invoice number suffix -> designed exception
PRICE_VARIANCE = {1042: Decimal("0.065"), 1013: Decimal("0.04"), 1029: Decimal("0.10"),
                  1071: Decimal("0.03"), 1096: Decimal("0.055")}  # fmt: skip
WITHIN_TOLERANCE = {1050: Decimal("0.012"), 1084: Decimal("0.018")}
QTY_VARIANCE = {1017, 1058, 1063, 1102}
N_SUPPLIER_INVOICES = 110


def _ts(d: date) -> datetime:
    return datetime.combine(d, time(10, 0), UTC)


def _q(v: Decimal, places: str = "0.01") -> Decimal:
    return v.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def reset_transactions(session: Session) -> None:
    """Remove seeded/agent-created transactional rows so seeding is repeatable."""
    session.execute(text("TRUNCATE goods_receipts, supplier_invoices, customer_invoices, "
                         "shipments RESTART IDENTITY"))  # fmt: skip
    session.execute(text("DELETE FROM po_lines WHERE po_id IN (SELECT id FROM purchase_orders "
                         "WHERE requisition_id IS NOT NULL)"))  # fmt: skip
    session.execute(text("DELETE FROM purchase_orders WHERE requisition_id IS NOT NULL"))
    session.execute(text("DELETE FROM purchase_requisitions"))
    session.execute(update(POLine).values(qty_received=0))
    session.execute(update(SOLine).values(qty_shipped=0))
    session.execute(update(Inventory).values(qty_reserved=0))


def seed_receipts(session: Session, rng: random.Random) -> list[PurchaseOrder]:
    """Post GRNs for POs whose legacy status says goods arrived."""
    pos = session.scalars(
        select(PurchaseOrder).options(selectinload(PurchaseOrder.lines))
        .where(PurchaseOrder.status.in_([POStatus.RECEIVED, POStatus.CLOSED,
                                         POStatus.PARTIALLY_RECEIVED]))
        .order_by(PurchaseOrder.po_number)
    ).all()  # fmt: skip
    grn_no = 70001
    for po in pos:
        rec_date = min((po.expected_date or po.order_date) + timedelta(days=rng.randint(-3, 5)),
                       AS_OF - timedelta(days=1))  # fmt: skip
        for i, ln in enumerate(po.lines):
            if po.status == POStatus.PARTIALLY_RECEIVED:
                if i % 2 == 1:
                    continue  # later lines still open
                qty = _q(ln.quantity * Decimal("0.5"), "1")
            else:
                qty = ln.quantity
            rejected = Decimal(0)
            if rng.random() < 0.04 and qty >= 20:
                rejected = _q(qty * Decimal("0.05"), "1")  # damaged on arrival
            session.add(GoodsReceipt(
                grn_number=f"GRN-{grn_no}", po_id=po.id, po_line_id=ln.id,
                warehouse_id=po.warehouse_id, qty_received=qty, qty_rejected=rejected,
                received_at=_ts(rec_date),
            ))  # fmt: skip
            ln.qty_received = qty - rejected
        grn_no += 1
    session.flush()
    return list(pos)


def seed_supplier_invoices(session: Session, rng: random.Random, received: list[PurchaseOrder]
                           ) -> None:  # fmt: skip
    """Create INV-1001.. against received POs, inject designed mismatches, run the 3-way match."""
    fully = [p for p in received if p.status in (POStatus.RECEIVED, POStatus.CLOSED)]
    open_sent = session.scalars(
        select(PurchaseOrder).options(selectinload(PurchaseOrder.lines))
        .where(PurchaseOrder.status == POStatus.SENT).order_by(PurchaseOrder.po_number)
    ).all()  # fmt: skip
    candidates = sorted(rng.sample(fully, min(N_SUPPLIER_INVOICES - 4, len(fully))),
                        key=lambda p: p.order_date)  # fmt: skip
    no_receipt_numbers = {1088: open_sent[0], 1099: open_sent[1], 1035: open_sent[2],
                          1107: open_sent[3]}  # fmt: skip
    it = iter(candidates)
    for n in range(1001, 1001 + N_SUPPLIER_INVOICES):
        po = no_receipt_numbers.get(n) or next(it)
        lines = []
        for k, ln in enumerate(po.lines):
            price = Decimal(ln.unit_price)
            qty = Decimal(ln.quantity) if n in no_receipt_numbers else Decimal(ln.qty_received)
            if k == 0 and n in PRICE_VARIANCE:
                price = _q(price * (1 + PRICE_VARIANCE[n]), "0.0001")
            if k == 0 and n in WITHIN_TOLERANCE:
                price = _q(price * (1 + WITHIN_TOLERANCE[n]), "0.0001")
            if k == 0 and n in QTY_VARIANCE:
                qty = qty + max(Decimal(5), _q(qty * Decimal("0.1"), "1"))
            lines.append({"po_line_id": ln.id, "quantity": float(qty),
                          "unit_price": float(price)})  # fmt: skip
        subtotal = _q(sum(Decimal(str(x["quantity"])) * Decimal(str(x["unit_price"]))
                          for x in lines))  # fmt: skip
        tax = _q(subtotal * TAX_RATE)
        inv_date = min(max(po.expected_date or po.order_date, po.order_date) +
                       timedelta(days=rng.randint(1, 12)), AS_OF - timedelta(days=1))  # fmt: skip
        session.add(SupplierInvoice(
            invoice_number=f"INV-{n}", supplier_id=po.supplier_id, po_id=po.id,
            invoice_date=inv_date, due_date=inv_date + timedelta(days=30), lines=lines,
            subtotal=subtotal, tax_amount=tax, total_amount=subtotal + tax,
        ))  # fmt: skip
        session.flush()
        result = three_way_match(session, f"INV-{n}", persist=True)
        inv = session.scalar(
            select(SupplierInvoice).where(SupplierInvoice.invoice_number == f"INV-{n}")
        )
        assert inv is not None
        if result["match_status"] == MatchStatus.MATCHED:
            inv.status = (SupplierInvoiceStatus.PAID if inv.due_date and inv.due_date < AS_OF
                          else SupplierInvoiceStatus.APPROVED)  # fmt: skip
    session.flush()


def seed_shipments_and_invoices(session: Session, rng: random.Random) -> None:
    """Shipments for shipped/invoiced/closed SOs; customer invoices for invoiced/closed SOs."""
    sos = session.scalars(
        select(SalesOrder).options(selectinload(SalesOrder.lines))
        .where(SalesOrder.status.in_([SOStatus.SHIPPED, SOStatus.INVOICED, SOStatus.CLOSED,
                                      SOStatus.PARTIALLY_SHIPPED]))
        .order_by(SalesOrder.so_number)
    ).all()  # fmt: skip
    shp_no, cinv_no = 80001, 60001
    for so in sos:
        ship_date = min(
            so.order_date + timedelta(days=rng.randint(1, 6)), AS_OF - timedelta(days=1)
        )
        lines = []
        for i, ln in enumerate(so.lines):
            if so.status == SOStatus.PARTIALLY_SHIPPED and i % 2 == 1:
                continue
            ln.qty_shipped = ln.quantity
            lines.append({"so_line_id": ln.id, "quantity": float(ln.quantity)})
        delivered = (
            so.status in (SOStatus.INVOICED, SOStatus.CLOSED) or (AS_OF - ship_date).days > 6
        )
        carrier = rng.choice(CARRIERS)
        shp = Shipment(
            shipment_number=f"SHP-{shp_no}", so_id=so.id, warehouse_id=so.warehouse_id,
            carrier=carrier, tracking_number=f"{carrier[:3].upper()}{rng.randint(10**9, 10**10)}",
            status=ShipmentStatus.DELIVERED if delivered else ShipmentStatus.IN_TRANSIT,
            lines=lines, shipped_at=_ts(ship_date),
            delivered_at=_ts(ship_date + timedelta(days=rng.randint(1, 4))) if delivered else None,
        )  # fmt: skip
        session.add(shp)
        session.flush()
        shp_no += 1
        if so.status in (SOStatus.INVOICED, SOStatus.CLOSED):
            due = ship_date + timedelta(days=30)
            status = (CustomerInvoiceStatus.PAID if so.status == SOStatus.CLOSED
                      else CustomerInvoiceStatus.OVERDUE if due < AS_OF
                      else CustomerInvoiceStatus.OPEN)  # fmt: skip
            session.add(CustomerInvoice(
                invoice_number=f"CINV-{cinv_no}", so_id=so.id, shipment_id=shp.id,
                customer_id=so.customer_id, invoice_date=ship_date, due_date=due,
                amount=so.total_amount, status=status,
            ))  # fmt: skip
            cinv_no += 1
    session.flush()


def seed_reservations_and_credit(session: Session) -> None:
    """Reserve stock for allocated demand; make credit-hold orders exceed the customer limit."""
    demand = session.execute(
        select(SOLine.item_id, SalesOrder.warehouse_id, SOLine.quantity - SOLine.qty_shipped)
        .join(SalesOrder)
        .where(SalesOrder.status.in_([SOStatus.ALLOCATED, SOStatus.PARTIALLY_SHIPPED]))
    ).all()  # fmt: skip
    totals: dict[tuple[int, int], Decimal] = {}
    for item_id, wh_id, q in demand:
        totals[(item_id, wh_id)] = totals.get((item_id, wh_id), Decimal(0)) + Decimal(q)
    for (item_id, wh_id), q in totals.items():
        inv = session.scalar(
            select(Inventory).where(Inventory.item_id == item_id, Inventory.warehouse_id == wh_id)
        )
        if inv is not None:
            inv.qty_reserved = min(q, inv.qty_on_hand)
    for so in session.scalars(select(SalesOrder).where(SalesOrder.status == SOStatus.CREDIT_HOLD)):
        cust = session.get(Customer, so.customer_id)
        assert cust is not None
        exposure = customer_open_ar(session, cust.id) + so.total_amount
        if exposure <= cust.credit_limit:
            # credit review cut the limit (demo scenario: explains *why* the order is on hold)
            cust.credit_limit = max(Decimal(500), _q(exposure * Decimal("0.6"), "1"))
    session.flush()


def run_seed(session: Session) -> dict[str, int]:
    """Seed all downstream data inside the caller's transaction and return row counts."""
    rng = random.Random(SEED)
    reset_transactions(session)
    received = seed_receipts(session, rng)
    seed_supplier_invoices(session, rng, received)
    seed_shipments_and_invoices(session, rng)
    seed_reservations_and_credit(session)
    counts = {}
    for table in ("goods_receipts", "supplier_invoices", "shipments", "customer_invoices"):
        counts[table] = session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
    return counts


def main() -> None:
    from opspilot.db.session import session_scope

    with session_scope() as session:
        counts = run_seed(session)
    for k, v in counts.items():
        print(f"{k:<20} {v:>5}")


if __name__ == "__main__":
    main()
