"""Procure-to-Pay endpoints: requisitions, POs, goods receipts and the SOAP supplier portal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from opspilot.api.schemas import (
    GRNCreate,
    POCreate,
    POOut,
    POSummary,
    RequisitionCreate,
    RequisitionOut,
)
from opspilot.db.enums import POStatus
from opspilot.db.models import PurchaseOrder, PurchaseRequisition
from opspilot.db.session import get_db
from opspilot.erp import p2p
from opspilot.erp.lookup import resolve_po, resolve_supplier
from opspilot.soap.client import PortalUnavailable, get_portal_client

router = APIRouter(tags=["procure-to-pay"])


@router.get("/purchase-requisitions", response_model=list[RequisitionOut])
def list_requisitions(limit: int = Query(50, le=500), db: Session = Depends(get_db)) -> list:
    """Most recent requisitions first."""
    return list(db.scalars(select(PurchaseRequisition)
                           .order_by(PurchaseRequisition.id.desc()).limit(limit)))  # fmt: skip


@router.post("/purchase-requisitions", response_model=RequisitionOut,
             status_code=status.HTTP_201_CREATED)  # fmt: skip
def create_requisition(
    body: RequisitionCreate, db: Session = Depends(get_db)
) -> PurchaseRequisition:
    """Raise a purchase requisition."""
    pr = p2p.create_requisition(db, body.item, body.quantity, body.warehouse, body.requested_by)
    db.commit()
    return pr


@router.get("/purchase-orders", response_model=list[POSummary])
def list_pos(
    status_: POStatus | None = Query(None, alias="status"),
    supplier: str | None = Query(None, description="SUP-xxxx"),
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list:
    """List purchase orders (newest first)."""
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.id.desc())
    if status_:
        stmt = stmt.where(PurchaseOrder.status == status_)
    if supplier:
        stmt = stmt.where(PurchaseOrder.supplier_id == resolve_supplier(db, supplier).id)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/purchase-orders/{po}", response_model=POOut)
def get_po(po: str, db: Session = Depends(get_db)) -> PurchaseOrder:
    """PO header + lines."""
    obj = resolve_po(db, po)
    return db.scalar(select(PurchaseOrder).options(selectinload(PurchaseOrder.lines))
                     .where(PurchaseOrder.id == obj.id))  # type: ignore[return-value]  # fmt: skip


@router.get("/purchase-orders/{po}/status")
def po_status(po: str, db: Session = Depends(get_db)) -> dict:
    """Business status: receipts, invoices and a readable summary."""
    return p2p.po_status(db, po)


@router.post("/purchase-orders", response_model=POOut, status_code=status.HTTP_201_CREATED)
def create_po(body: POCreate, db: Session = Depends(get_db)) -> PurchaseOrder:
    """Convert a requisition into a PO (auto-approved up to the approval limit)."""
    po = p2p.create_purchase_order(db, body.requisition_id, body.supplier_id, body.created_by)
    db.commit()
    db.refresh(po)
    return po


@router.post("/purchase-orders/{po}/approve", response_model=POSummary)
def approve_po(po: str, approver: str = "manager", db: Session = Depends(get_db)) -> PurchaseOrder:
    """Manager approval for POs above the auto-approval limit."""
    obj = resolve_po(db, po)
    if obj.status != POStatus.PENDING_APPROVAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{obj.po_number} is {obj.status}")
    obj.status, obj.approved_by = POStatus.APPROVED, approver
    db.commit()
    return obj


@router.post("/purchase-orders/{po}/cancel", response_model=POSummary)
def cancel_po(po: str, db: Session = Depends(get_db)) -> PurchaseOrder:
    """Cancel a PO that has not been received yet."""
    obj = resolve_po(db, po)
    if obj.status not in (POStatus.DRAFT, POStatus.PENDING_APPROVAL, POStatus.APPROVED,
                          POStatus.SENT):  # fmt: skip
        raise HTTPException(status.HTTP_409_CONFLICT, f"{obj.po_number} is {obj.status}")
    obj.status = POStatus.CANCELLED
    db.commit()
    return obj


@router.post("/goods-receipts", status_code=status.HTTP_201_CREATED)
def post_grn(body: GRNCreate, db: Session = Depends(get_db)) -> dict:
    """Post a goods receipt; updates stock on hand and PO status."""
    grns = p2p.post_goods_receipt(db, body.po_number, [ln.model_dump() for ln in body.lines])
    db.commit()
    return {"grn_number": grns[0].grn_number, "lines": len(grns),
            "po": p2p.po_status(db, body.po_number)["status"]}  # fmt: skip


# ---- REST -> SOAP bridge (legacy supplier portal via zeep)
@router.get("/supplier-portal/quotes")
def portal_quotes(item: str, qty: float = Query(..., gt=0)) -> list[dict]:
    """Live quotes from the SOAP Supplier Portal."""
    try:
        return get_portal_client().get_supplier_quote(item, qty)
    except PortalUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/supplier-portal/delivery-status/{po}")
def portal_delivery_status(po: str) -> dict:
    """Delivery status for a PO from the SOAP Supplier Portal."""
    try:
        return get_portal_client().get_delivery_status(po)
    except PortalUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
