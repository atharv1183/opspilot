"""Order-to-Cash endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.api.schemas import SOCreate, SOSummary
from opspilot.db.enums import SOStatus
from opspilot.db.models import SalesOrder
from opspilot.db.session import get_db
from opspilot.erp import o2c

router = APIRouter(prefix="/sales-orders", tags=["order-to-cash"])


@router.get("", response_model=list[SOSummary])
def list_sales_orders(
    status_: SOStatus | None = Query(None, alias="status"),
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list:
    """List sales orders (newest first)."""
    stmt = select(SalesOrder).order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc())
    if status_:
        stmt = stmt.where(SalesOrder.status == status_)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/{so}")
def get_sales_order(so: str, db: Session = Depends(get_db)) -> dict:
    """Sales order with lines, shipments, invoices and credit status."""
    return o2c.sales_order_status(db, so)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_sales_order(body: SOCreate, db: Session = Depends(get_db)) -> dict:
    """Create a sales order (credit-checked: may land on CREDIT_HOLD)."""
    so = o2c.create_sales_order(
        db,
        body.customer_code,
        body.warehouse,
        [ln.model_dump() for ln in body.lines],
        body.requested_date,
    )
    db.commit()
    return o2c.sales_order_status(db, so.so_number)
