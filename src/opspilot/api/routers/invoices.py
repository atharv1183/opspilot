"""Supplier (AP) and customer (AR) invoices, incl. 3-way match."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.api.schemas import CustomerInvoiceOut, SupplierInvoiceOut
from opspilot.db.enums import CustomerInvoiceStatus, MatchStatus, SupplierInvoiceStatus
from opspilot.db.models import CustomerInvoice, SupplierInvoice
from opspilot.db.session import get_db
from opspilot.erp import p2p
from opspilot.erp.lookup import resolve_supplier_invoice

router = APIRouter(tags=["invoices"])


@router.get("/supplier-invoices", response_model=list[SupplierInvoiceOut])
def list_supplier_invoices(
    status_: SupplierInvoiceStatus | None = Query(None, alias="status"),
    match_status: MatchStatus | None = None,
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
) -> list:
    """AP invoices, filterable by status / match status (e.g. ON_HOLD)."""
    stmt = select(SupplierInvoice).order_by(SupplierInvoice.invoice_number)
    if status_:
        stmt = stmt.where(SupplierInvoice.status == status_)
    if match_status:
        stmt = stmt.where(SupplierInvoice.match_status == match_status)
    return list(db.scalars(stmt.limit(limit)))


@router.get("/supplier-invoices/{invoice}", response_model=SupplierInvoiceOut)
def get_supplier_invoice(invoice: str, db: Session = Depends(get_db)) -> SupplierInvoice:
    return resolve_supplier_invoice(db, invoice)


@router.get("/supplier-invoices/{invoice}/three-way-match")
def three_way_match(invoice: str, db: Session = Depends(get_db)) -> dict:
    """Explain PO vs goods receipt vs invoice for one supplier invoice."""
    return p2p.three_way_match(db, invoice)


@router.post("/supplier-invoices/{invoice}/rematch")
def rematch(invoice: str, db: Session = Depends(get_db)) -> dict:
    """Re-run the 3-way match (e.g. after a late GRN) and persist the result."""
    result = p2p.three_way_match(db, invoice, persist=True)
    inv = resolve_supplier_invoice(db, invoice)
    if (
        result["match_status"] == MatchStatus.MATCHED
        and inv.status == SupplierInvoiceStatus.ON_HOLD
    ):
        inv.status, inv.hold_reason = SupplierInvoiceStatus.APPROVED, None
    db.commit()
    return p2p.three_way_match(db, invoice)


@router.get("/customer-invoices", response_model=list[CustomerInvoiceOut])
def list_customer_invoices(
    status_: CustomerInvoiceStatus | None = Query(None, alias="status"),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
) -> list:
    """AR invoices (e.g. status=OVERDUE for collections)."""
    stmt = select(CustomerInvoice).order_by(CustomerInvoice.invoice_number)
    if status_:
        stmt = stmt.where(CustomerInvoice.status == status_)
    return list(db.scalars(stmt.limit(limit)))


@router.get("/customer-invoices/{invoice}", response_model=CustomerInvoiceOut)
def get_customer_invoice(invoice: str, db: Session = Depends(get_db)) -> CustomerInvoice:
    inv = db.scalar(
        select(CustomerInvoice).where(CustomerInvoice.invoice_number == invoice.upper())
    )
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Customer invoice {invoice} not found")
    return inv
