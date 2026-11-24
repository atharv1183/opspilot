"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opspilot.db.enums import UnitOfMeasure


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------- items
class ItemBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    category: str | None = None
    uom: UnitOfMeasure = UnitOfMeasure.EA
    standard_cost: Decimal = Field(ge=0)
    list_price: Decimal = Field(ge=0)
    lead_time_days: int = Field(default=7, ge=0, le=365)


class ItemCreate(ItemBase):
    sku: str = Field(pattern=r"^ITM-\d{3,6}$", examples=["ITM-2001"])


class ItemUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    standard_cost: Decimal | None = Field(default=None, ge=0)
    list_price: Decimal | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    is_active: bool | None = None


class ItemOut(ORM, ItemBase):
    id: int
    sku: str
    is_active: bool
    preferred_supplier_id: int | None


# ----------------------------------------------------------------- inventory
class InventoryRow(BaseModel):
    sku: str
    name: str
    warehouse: str
    qty_on_hand: float
    qty_reserved: float
    reorder_level: float
    reorder_qty: float
    below_reorder_level: bool


class InventoryAdjust(BaseModel):
    """Cycle-count result or reorder-parameter change for one item/warehouse."""

    qty_on_hand: Decimal | None = Field(default=None, ge=0)
    reorder_level: Decimal | None = Field(default=None, ge=0)
    reorder_qty: Decimal | None = Field(default=None, ge=0)
    reason: str = "cycle count"


# ----------------------------------------------------------------- purchasing
class RequisitionCreate(BaseModel):
    item: str = Field(description="SKU or item name", examples=["ITM-1088"])
    quantity: float = Field(gt=0)
    warehouse: str | None = Field(default=None, examples=["WH-B"])
    requested_by: str = "api-user"


class RequisitionOut(ORM):
    id: int
    pr_number: str
    item_id: int
    warehouse_id: int
    quantity: Decimal
    status: str
    needed_by: date | None
    requested_by: str


class POCreate(BaseModel):
    requisition_id: str = Field(examples=["PR-5001"])
    supplier_id: str = Field(examples=["SUP-0123"])
    created_by: str = "api-user"


class POLineOut(ORM):
    line_no: int
    item_id: int
    quantity: Decimal
    unit_price: Decimal
    qty_received: Decimal


class POOut(ORM):
    id: int
    po_number: str
    supplier_id: int
    warehouse_id: int
    requisition_id: int | None
    status: str
    order_date: date
    expected_date: date | None
    total_amount: Decimal
    approved_by: str | None
    lines: list[POLineOut]


class POSummary(ORM):
    id: int
    po_number: str
    supplier_id: int
    status: str
    order_date: date
    expected_date: date | None
    total_amount: Decimal


class GRNLine(BaseModel):
    line_no: int
    qty_received: Decimal = Field(gt=0)
    qty_rejected: Decimal = Field(default=Decimal(0), ge=0)


class GRNCreate(BaseModel):
    po_number: str
    lines: list[GRNLine] = Field(min_length=1)


# ----------------------------------------------------------------- sales
class SOLineIn(BaseModel):
    item: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0, description="defaults to list price")


class SOCreate(BaseModel):
    customer_code: str = Field(examples=["CUS-00001"])
    warehouse: str = Field(examples=["WH-A"])
    requested_date: date | None = None
    lines: list[SOLineIn] = Field(min_length=1)


class SOSummary(ORM):
    id: int
    so_number: str
    customer_id: int
    status: str
    order_date: date
    requested_date: date | None
    total_amount: Decimal


# ----------------------------------------------------------------- invoices
class SupplierInvoiceOut(ORM):
    id: int
    invoice_number: str
    supplier_id: int
    po_id: int
    invoice_date: date
    due_date: date | None
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    match_status: str
    status: str
    hold_reason: str | None
    lines: list[dict[str, Any]]


class CustomerInvoiceOut(ORM):
    id: int
    invoice_number: str
    so_id: int
    customer_id: int
    invoice_date: date
    due_date: date
    amount: Decimal
    status: str


# ----------------------------------------------------------------- agent
class ChatRequest(BaseModel):
    message: str | None = Field(default=None, description="User question / instruction")
    thread_id: str | None = Field(default=None, description="Conversation id (memory)")
    approve: bool | None = Field(
        default=None,
        description="Answer to a pending human-approval request: true = execute, false = reject",
    )


class ToolCallOut(BaseModel):
    name: str
    args: dict[str, Any]


class ChatResponse(BaseModel):
    thread_id: str
    answer: str | None
    tool_calls: list[ToolCallOut]
    pending_approval: dict[str, Any] | None = None
    sources: list[str] = []
    provider: str
    latency_ms: int
    timestamp: datetime
