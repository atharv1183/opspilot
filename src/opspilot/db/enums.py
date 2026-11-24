"""Status / type enums used across the ERP schema (stored as native Postgres enums)."""

from __future__ import annotations

import enum


class SupplierStatus(enum.StrEnum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"


class UnitOfMeasure(enum.StrEnum):
    EA = "EA"
    BOX = "BOX"
    KG = "KG"
    L = "L"
    M = "M"


class RequisitionStatus(enum.StrEnum):
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    CONVERTED = "CONVERTED"
    REJECTED = "REJECTED"


class POStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class MatchStatus(enum.StrEnum):
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QTY_VARIANCE = "QTY_VARIANCE"
    NO_RECEIPT = "NO_RECEIPT"


class SupplierInvoiceStatus(enum.StrEnum):
    RECEIVED = "RECEIVED"
    ON_HOLD = "ON_HOLD"
    APPROVED = "APPROVED"
    PAID = "PAID"


class SOStatus(enum.StrEnum):
    OPEN = "OPEN"
    CREDIT_HOLD = "CREDIT_HOLD"
    ALLOCATED = "ALLOCATED"
    PARTIALLY_SHIPPED = "PARTIALLY_SHIPPED"
    SHIPPED = "SHIPPED"
    INVOICED = "INVOICED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ShipmentStatus(enum.StrEnum):
    PENDING = "PENDING"
    PICKED = "PICKED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"


class CustomerInvoiceStatus(enum.StrEnum):
    OPEN = "OPEN"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    VOID = "VOID"
