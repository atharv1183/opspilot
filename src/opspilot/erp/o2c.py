"""Order-to-Cash: sales order status with shipments, invoices and credit exposure."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from opspilot.db.enums import CustomerInvoiceStatus, SOStatus
from opspilot.db.models import Customer, CustomerInvoice, SalesOrder, Shipment, SOLine
from opspilot.erp.lookup import resolve_so


def customer_open_ar(session: Session, customer_id: int) -> Decimal:
    """Open accounts receivable (OPEN + OVERDUE customer invoices)."""
    return Decimal(session.scalar(
        select(func.coalesce(func.sum(CustomerInvoice.amount), 0)).where(
            CustomerInvoice.customer_id == customer_id,
            CustomerInvoice.status.in_([CustomerInvoiceStatus.OPEN, CustomerInvoiceStatus.OVERDUE]),
        )
    ) or 0)  # fmt: skip


def sales_order_status(session: Session, so_ref: str | int) -> dict:
    """Status of a sales order: lines shipped, shipments/tracking, invoices, credit hold reason."""
    so = resolve_so(session, so_ref)
    so = session.scalar(
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer), joinedload(SalesOrder.warehouse),
                 joinedload(SalesOrder.lines).joinedload(SOLine.item))
        .where(SalesOrder.id == so.id)
    )  # fmt: skip
    assert so is not None
    ships = session.scalars(select(Shipment).where(Shipment.so_id == so.id)
                            .order_by(Shipment.shipment_number)).all()  # fmt: skip
    invs = session.scalars(select(CustomerInvoice).where(CustomerInvoice.so_id == so.id)).all()
    lines = [{"line_no": ln.line_no, "sku": ln.item.sku, "name": ln.item.name,
              "quantity": float(ln.quantity), "qty_shipped": float(ln.qty_shipped),
              "unit_price": float(ln.unit_price)} for ln in so.lines]  # fmt: skip
    ordered = sum(x["quantity"] for x in lines)
    shipped = sum(x["qty_shipped"] for x in lines)
    parts = [
        f"{so.so_number} for {so.customer.name} ({so.customer.customer_code}), "
        f"${float(so.total_amount):,.2f}, is {so.status}. Ordered {so.order_date}, requested "
        f"{so.requested_date}, ships from {so.warehouse.code}. Shipped {shipped:g} of "
        f"{ordered:g} units."
    ]
    if ships:
        parts.append("Shipments: " + ", ".join(
            f"{s.shipment_number} via {s.carrier} ({s.tracking_number}) {s.status}" for s in ships
        ) + ".")  # fmt: skip
    if invs:
        parts.append("Invoices: " + ", ".join(
            f"{i.invoice_number} ${float(i.amount):,.2f} {i.status} (due {i.due_date})"
            for i in invs
        ) + ".")  # fmt: skip
    credit = None
    if so.status == SOStatus.CREDIT_HOLD:
        ar = customer_open_ar(session, so.customer_id)
        exposure = ar + so.total_amount
        credit = {"credit_limit": float(so.customer.credit_limit), "open_ar": float(ar),
                  "exposure_with_order": float(exposure)}  # fmt: skip
        parts.append(
            f"On credit hold: open AR ${float(ar):,.2f} + this order = ${float(exposure):,.2f} "
            f"vs credit limit ${float(so.customer.credit_limit):,.2f}; release needs credit "
            "control approval."
        )
    return {
        "so_number": so.so_number, "status": str(so.status),
        "customer": {"code": so.customer.customer_code, "name": so.customer.name},
        "warehouse": so.warehouse.code, "order_date": so.order_date.isoformat(),
        "requested_date": so.requested_date.isoformat() if so.requested_date else None,
        "total_amount": float(so.total_amount), "lines": lines,
        "shipments": [{"shipment_number": s.shipment_number, "carrier": s.carrier,
                       "tracking_number": s.tracking_number, "status": str(s.status),
                       "shipped_at": s.shipped_at.date().isoformat() if s.shipped_at else None,
                       "delivered_at": s.delivered_at.date().isoformat() if s.delivered_at
                       else None} for s in ships],
        "invoices": [{"invoice_number": i.invoice_number, "amount": float(i.amount),
                      "status": str(i.status), "due_date": i.due_date.isoformat()} for i in invs],
        "credit": credit,
        "summary": " ".join(parts),
    }  # fmt: skip


def create_sales_order(
    session: Session,
    customer_code: str,
    warehouse_ref: str,
    lines: list[dict],
    requested_date: date | None = None,
) -> SalesOrder:
    """Create a sales order; it goes on CREDIT_HOLD if open AR + order value > credit limit."""
    from sqlalchemy import Integer, cast

    from opspilot.erp import ERPError
    from opspilot.erp.lookup import resolve_item, resolve_warehouse

    cust = session.scalar(select(Customer).where(Customer.customer_code == customer_code))
    if cust is None:
        raise ERPError(f"Customer '{customer_code}' not found.")
    wh = resolve_warehouse(session, warehouse_ref)
    if wh is None:
        raise ERPError("A shipping warehouse is required.")
    so_lines = []
    for n, spec in enumerate(lines, 1):
        item = resolve_item(session, spec["item"])
        price = spec.get("unit_price")
        so_lines.append(SOLine(line_no=n, item_id=item.id, quantity=Decimal(str(spec["quantity"])),
                               unit_price=Decimal(str(price)) if price is not None
                               else item.list_price))  # fmt: skip
    total = sum((ln.quantity * ln.unit_price for ln in so_lines), Decimal(0))
    exposure = customer_open_ar(session, cust.id) + total
    max_no = session.scalar(
        select(func.max(cast(func.split_part(SalesOrder.so_number, "-", 2), Integer)))
    )
    so = SalesOrder(
        so_number=f"SO-{(max_no or 20000) + 1}", customer_id=cust.id, warehouse_id=wh.id,
        order_date=date.today(), requested_date=requested_date,
        status=SOStatus.CREDIT_HOLD if exposure > cust.credit_limit else SOStatus.OPEN,
        total_amount=total, lines=so_lines,
    )  # fmt: skip
    session.add(so)
    session.flush()
    return so
