"""Item master CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from opspilot.api.schemas import ItemCreate, ItemOut, ItemUpdate
from opspilot.db.models import Item
from opspilot.db.session import get_db

router = APIRouter(prefix="/items", tags=["items"])


def _get(db: Session, sku: str) -> Item:
    item = db.scalar(select(Item).where(Item.sku == sku.upper()))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Item {sku} not found")
    return item


@router.get("", response_model=list[ItemOut])
def list_items(
    q: str | None = Query(None, description="search in SKU / name"),
    category: str | None = None,
    active: bool | None = True,
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Item]:
    """List / search items."""
    stmt = select(Item).order_by(Item.sku)
    if q:
        stmt = stmt.where(Item.name.ilike(f"%{q}%") | Item.sku.ilike(f"%{q}%"))
    if category:
        stmt = stmt.where(Item.category == category)
    if active is not None:
        stmt = stmt.where(Item.is_active.is_(active))
    return list(db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/{sku}", response_model=ItemOut)
def get_item(sku: str, db: Session = Depends(get_db)) -> Item:
    """Get one item by SKU."""
    return _get(db, sku)


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemCreate, db: Session = Depends(get_db)) -> Item:
    """Create an item."""
    if db.scalar(select(Item).where(Item.sku == body.sku)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Item {body.sku} already exists")
    item = Item(**body.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{sku}", response_model=ItemOut)
def update_item(sku: str, body: ItemUpdate, db: Session = Depends(get_db)) -> Item:
    """Partially update an item."""
    item = _get(db, sku)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{sku}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_item(sku: str, db: Session = Depends(get_db)) -> None:
    """Soft-delete (deactivate) an item; history must stay referentially intact."""
    item = _get(db, sku)
    item.is_active = False
    db.commit()
