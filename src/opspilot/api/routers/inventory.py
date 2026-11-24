"""Inventory, reorder exceptions and available-to-promise."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from opspilot.api.schemas import InventoryAdjust, InventoryRow
from opspilot.db.models import Inventory, Item, Warehouse
from opspilot.db.session import get_db
from opspilot.erp import inventory as inv_svc
from opspilot.erp.lookup import resolve_item, resolve_warehouse

router = APIRouter(tags=["inventory"])


def _row(r: Inventory) -> InventoryRow:
    return InventoryRow(
        sku=r.item.sku, name=r.item.name, warehouse=r.warehouse.code,
        qty_on_hand=float(r.qty_on_hand), qty_reserved=float(r.qty_reserved),
        reorder_level=float(r.reorder_level), reorder_qty=float(r.reorder_qty),
        below_reorder_level=r.qty_on_hand <= r.reorder_level,
    )  # fmt: skip


@router.get("/inventory", response_model=list[InventoryRow])
def list_inventory(
    warehouse: str | None = Query(None, examples=["WH-B"]),
    below_reorder: bool = False,
    limit: int = Query(100, le=2000),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[InventoryRow]:
    """Inventory rows, optionally filtered by warehouse and reorder exceptions."""
    stmt = (select(Inventory).join(Item).join(Warehouse)
            .options(joinedload(Inventory.item), joinedload(Inventory.warehouse))
            .order_by(Warehouse.code, Item.sku))  # fmt: skip
    wh = resolve_warehouse(db, warehouse)
    if wh:
        stmt = stmt.where(Inventory.warehouse_id == wh.id)
    if below_reorder:
        stmt = stmt.where(Inventory.qty_on_hand <= Inventory.reorder_level)
    return [_row(r) for r in db.scalars(stmt.limit(limit).offset(offset))]


@router.get("/inventory/low-stock")
def low_stock(warehouse: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Items at or below reorder level, most critical first."""
    return inv_svc.low_stock_items(db, warehouse)


@router.get("/inventory/{item}")
def item_inventory(item: str, warehouse: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Stock position of an item (SKU or name) per warehouse."""
    return inv_svc.inventory_level(db, item, warehouse)


@router.patch("/inventory/{item}/{warehouse}", response_model=InventoryRow)
def adjust_inventory(
    item: str, warehouse: str, body: InventoryAdjust, db: Session = Depends(get_db)
) -> InventoryRow:
    """Post a cycle-count result or change reorder parameters."""
    it = resolve_item(db, item)
    wh = resolve_warehouse(db, warehouse)
    row = db.scalar(select(Inventory).options(joinedload(Inventory.item),
                                              joinedload(Inventory.warehouse))
                    .where(Inventory.item_id == it.id, Inventory.warehouse_id == wh.id))  # type: ignore[union-attr]  # fmt: skip
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{it.sku} is not stocked in {warehouse}")
    for k, v in body.model_dump(exclude_unset=True, exclude={"reason"}).items():
        setattr(row, k, v)
    if body.qty_on_hand is not None:
        row.last_counted_at = date.today()
    db.commit()
    db.refresh(row)
    return _row(row)


@router.get("/atp")
def check_atp(
    item: str = Query(..., examples=["ITM-1088"]),
    qty: float = Query(..., gt=0),
    on: date = Query(..., alias="date", description="promise date (YYYY-MM-DD)"),
    warehouse: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Available-to-promise check: can we ship ``qty`` of ``item`` by ``date``?"""
    return inv_svc.available_to_promise(db, item, qty, on, warehouse)
