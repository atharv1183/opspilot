"""The 14 ERP business tables (public schema) plus the RAG chunk table (``rag`` schema).

Procure-to-Pay:  purchase_requisitions -> purchase_orders/po_lines -> goods_receipts
                 -> supplier_invoices (3-way match against PO + GRN)
Order-to-Cash:   sales_orders/so_lines -> shipments -> customer_invoices
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opspilot.config import get_settings
from opspilot.db.base import Base, TimestampMixin
from opspilot.db.enums import (
    CustomerInvoiceStatus,
    MatchStatus,
    POStatus,
    RequisitionStatus,
    ShipmentStatus,
    SOStatus,
    SupplierInvoiceStatus,
    SupplierStatus,
    UnitOfMeasure,
)

Money = Numeric(14, 2)
Qty = Numeric(14, 3)


def _enum(e: type, name: str) -> Enum:
    """Native Postgres enum that stores the member *values*."""
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


# --------------------------------------------------------------------------- master data
class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(30), unique=True)
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str | None] = mapped_column(String(80))
    payment_terms: Mapped[str] = mapped_column(String(20), default="NET30")
    status: Mapped[SupplierStatus] = mapped_column(
        _enum(SupplierStatus, "supplier_status"), default=SupplierStatus.PENDING
    )
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    legacy_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(80))
    segment: Mapped[str | None] = mapped_column(String(40))
    credit_limit: Mapped[Decimal] = mapped_column(Money, default=0)


class Warehouse(TimestampMixin, Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(80))


class Item(TimestampMixin, Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str | None] = mapped_column(String(60))
    uom: Mapped[UnitOfMeasure] = mapped_column(_enum(UnitOfMeasure, "unit_of_measure"))
    standard_cost: Mapped[Decimal] = mapped_column(Money)
    list_price: Mapped[Decimal] = mapped_column(Money)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    preferred_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    is_active: Mapped[bool] = mapped_column(default=True)

    preferred_supplier: Mapped[Supplier | None] = relationship()


class Inventory(TimestampMixin, Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("item_id", "warehouse_id", name="uq_inventory_item_warehouse"),
        CheckConstraint("qty_on_hand >= 0", name="qty_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    qty_on_hand: Mapped[Decimal] = mapped_column(Qty, default=0)
    qty_reserved: Mapped[Decimal] = mapped_column(Qty, default=0)
    reorder_level: Mapped[Decimal] = mapped_column(Qty, default=0)
    reorder_qty: Mapped[Decimal] = mapped_column(Qty, default=0)
    last_counted_at: Mapped[date | None] = mapped_column(Date)

    item: Mapped[Item] = relationship()
    warehouse: Mapped[Warehouse] = relationship()


# --------------------------------------------------------------------------- procure-to-pay
class PurchaseRequisition(TimestampMixin, Base):
    __tablename__ = "purchase_requisitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    pr_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    requested_by: Mapped[str] = mapped_column(String(80), default="opspilot-agent")
    needed_by: Mapped[date | None] = mapped_column(Date)
    status: Mapped[RequisitionStatus] = mapped_column(
        _enum(RequisitionStatus, "requisition_status"), default=RequisitionStatus.SUBMITTED
    )

    item: Mapped[Item] = relationship()
    warehouse: Mapped[Warehouse] = relationship()


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    requisition_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_requisitions.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[POStatus] = mapped_column(_enum(POStatus, "po_status"), default=POStatus.DRAFT)
    order_date: Mapped[date] = mapped_column(Date)
    expected_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    total_amount: Mapped[Decimal] = mapped_column(Money, default=0)
    created_by: Mapped[str] = mapped_column(String(80), default="legacy-migration")
    approved_by: Mapped[str | None] = mapped_column(String(80))

    supplier: Mapped[Supplier] = relationship()
    warehouse: Mapped[Warehouse] = relationship()
    requisition: Mapped[PurchaseRequisition | None] = relationship()
    lines: Mapped[list[POLine]] = relationship(
        back_populates="po", cascade="all, delete-orphan", order_by="POLine.line_no"
    )


class POLine(Base):
    __tablename__ = "po_lines"
    __table_args__ = (UniqueConstraint("po_id", "line_no", name="uq_po_lines_po_line"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    qty_received: Mapped[Decimal] = mapped_column(Qty, default=0)

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()


class GoodsReceipt(TimestampMixin, Base):
    """One row per received PO line (a GRN number may span several rows)."""

    __tablename__ = "goods_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    grn_number: Mapped[str] = mapped_column(String(20), index=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_lines.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    qty_received: Mapped[Decimal] = mapped_column(Qty)
    qty_rejected: Mapped[Decimal] = mapped_column(Qty, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    po_line: Mapped[POLine] = relationship()


class SupplierInvoice(TimestampMixin, Base):
    """AP invoice. ``lines`` = [{po_line_id, quantity, unit_price}] billed by the supplier."""

    __tablename__ = "supplier_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    subtotal: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Money)
    match_status: Mapped[MatchStatus] = mapped_column(
        _enum(MatchStatus, "match_status"), default=MatchStatus.PENDING
    )
    status: Mapped[SupplierInvoiceStatus] = mapped_column(
        _enum(SupplierInvoiceStatus, "supplier_invoice_status"),
        default=SupplierInvoiceStatus.RECEIVED,
    )
    hold_reason: Mapped[str | None] = mapped_column(Text)

    supplier: Mapped[Supplier] = relationship()
    po: Mapped[PurchaseOrder] = relationship()


# --------------------------------------------------------------------------- order-to-cash
class SalesOrder(TimestampMixin, Base):
    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    so_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    order_date: Mapped[date] = mapped_column(Date)
    requested_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[SOStatus] = mapped_column(_enum(SOStatus, "so_status"), default=SOStatus.OPEN)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    total_amount: Mapped[Decimal] = mapped_column(Money, default=0)

    customer: Mapped[Customer] = relationship()
    warehouse: Mapped[Warehouse] = relationship()
    lines: Mapped[list[SOLine]] = relationship(
        back_populates="so", cascade="all, delete-orphan", order_by="SOLine.line_no"
    )


class SOLine(Base):
    __tablename__ = "so_lines"
    __table_args__ = (UniqueConstraint("so_id", "line_no", name="uq_so_lines_so_line"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    qty_shipped: Mapped[Decimal] = mapped_column(Qty, default=0)

    so: Mapped[SalesOrder] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()


class Shipment(TimestampMixin, Base):
    """Outbound delivery. ``lines`` = [{so_line_id, quantity}]."""

    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    carrier: Mapped[str | None] = mapped_column(String(60))
    tracking_number: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[ShipmentStatus] = mapped_column(
        _enum(ShipmentStatus, "shipment_status"), default=ShipmentStatus.PENDING
    )
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerInvoice(TimestampMixin, Base):
    __tablename__ = "customer_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[CustomerInvoiceStatus] = mapped_column(
        _enum(CustomerInvoiceStatus, "customer_invoice_status"),
        default=CustomerInvoiceStatus.OPEN,
    )


# --------------------------------------------------------------------------- RAG (separate schema)
class DocChunk(Base):
    """Embedded SOP chunk. Lives in the ``rag`` schema, so it is not one of the 14 ERP tables."""

    __tablename__ = "doc_chunks"
    __table_args__ = (
        Index(
            "ix_rag_doc_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        {"schema": "rag"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_name: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(200))
    section: Mapped[str | None] = mapped_column(String(200))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dim))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


ERP_TABLES = [
    "suppliers", "customers", "items", "warehouses", "inventory",
    "purchase_requisitions", "purchase_orders", "po_lines", "goods_receipts",
    "supplier_invoices", "sales_orders", "so_lines", "shipments", "customer_invoices",
]  # fmt: skip
