"""Pydantic models that validate + clean one legacy CSV row each.

Construct with ``Model.from_row(csv_dict)``; a ``pydantic.ValidationError`` means the row is
rejected and :func:`rejection_reason` turns it into a short reason for the report.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from migration import cleaning as c


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True)


def rejection_reason(err: ValidationError) -> str:
    """First validation error message without pydantic's 'Value error, ' prefix."""
    first = err.errors()[0]
    msg = str(first.get("msg", "invalid row")).removeprefix("Value error, ")
    if first.get("type") == "missing":
        return f"missing {first['loc'][0]}"
    return msg


class SupplierRecord(_Row):
    legacy_code: str
    name: str
    tax_id: str | None
    email: str | None
    phone: str | None
    country: str | None
    payment_terms: str
    status: str
    rating: Decimal | None
    created: date | None

    _name = field_validator("name", mode="before")(lambda v: c.clean_name(v))
    _tax = field_validator("tax_id", mode="before")(lambda v: c.normalize_tax_id(v))
    _email = field_validator("email", mode="before")(lambda v: c.normalize_email(v))
    _country = field_validator("country", mode="before")(lambda v: c.normalize_country(v))
    _terms = field_validator("payment_terms", mode="before")(lambda v: c.normalize_terms(v))
    _status = field_validator("status", mode="before")(
        lambda v: c.map_status(v, c.SUPPLIER_STATUS_MAP, field="supplier status")
    )
    _created = field_validator("created", mode="before")(lambda v: c.parse_optional_date(v))

    @field_validator("rating", mode="before")
    @classmethod
    def _rating(cls, v: Any) -> Decimal | None:
        try:
            r = c.parse_decimal(v)
        except ValueError:
            return None
        return r if Decimal(0) <= r <= Decimal(5) else None

    @classmethod
    def from_row(cls, row: dict[str, str]) -> SupplierRecord:
        return cls(
            legacy_code=row["SUPP_NO"].strip(), name=row["SUPP_NAME"], tax_id=row["TAX_ID"],
            email=row["CONTACT_EMAIL"], phone=row["PHONE"].strip() or None,
            country=row["COUNTRY"], payment_terms=row["TERMS"], status=row["STATUS"],
            rating=row["RATING"], created=row["CREATED"],
        )  # fmt: skip


class CustomerRecord(_Row):
    legacy_code: str
    name: str
    email: str | None
    country: str | None
    segment: str | None
    credit_limit: Decimal | None  # None = invalid in legacy -> defaulted with a warning
    created: date | None

    _name = field_validator("name", mode="before")(lambda v: c.clean_name(v))
    _email = field_validator("email", mode="before")(lambda v: c.normalize_email(v))
    _country = field_validator("country", mode="before")(lambda v: c.normalize_country(v))
    _created = field_validator("created", mode="before")(lambda v: c.parse_optional_date(v))

    @field_validator("credit_limit", mode="before")
    @classmethod
    def _credit(cls, v: Any) -> Decimal | None:
        try:
            amount = c.parse_decimal(v, field="credit limit")
        except ValueError:
            return None
        return amount if amount >= 0 else None

    @classmethod
    def from_row(cls, row: dict[str, str]) -> CustomerRecord:
        return cls(
            legacy_code=row["CUST_NO"].strip(), name=row["CUST_NAME"], email=row["EMAIL"],
            country=row["COUNTRY"], segment=row["SEGMENT"].strip() or None,
            credit_limit=row["CREDIT_LIMIT"], created=row["CREATED"],
        )  # fmt: skip


class ItemRecord(_Row):
    sku: str
    name: str
    category: str | None
    uom: str
    standard_cost: Decimal
    list_price: Decimal | None
    lead_time_days: int
    preferred_supplier_code: str | None

    _sku = field_validator("sku", mode="before")(lambda v: c.normalize_item_no(v))
    _name = field_validator("name", mode="before")(lambda v: c.clean_name(v))
    _uom = field_validator("uom", mode="before")(lambda v: c.normalize_uom(v))
    _cost = field_validator("standard_cost", mode="before")(
        lambda v: c.parse_quantity(v, field="standard cost")
    )
    _lead = field_validator("lead_time_days", mode="before")(lambda v: c.parse_lead_time(v))

    @field_validator("list_price", mode="before")
    @classmethod
    def _price(cls, v: Any) -> Decimal | None:
        try:
            return c.parse_quantity(v, field="list price")
        except ValueError:
            return None

    @classmethod
    def from_row(cls, row: dict[str, str]) -> ItemRecord:
        return cls(
            sku=row["ITEM_NO"], name=row["DESCRIPTION"], category=row["CATEGORY"].strip() or None,
            uom=row["UOM"], standard_cost=row["STD_COST"], list_price=row["LIST_PRICE"],
            lead_time_days=row["LEAD_TIME"],
            preferred_supplier_code=row["PREF_SUPPLIER"].strip() or None,
        )  # fmt: skip


class InventoryRecord(_Row):
    sku: str
    warehouse: str
    qty_on_hand: Decimal
    reorder_level: Decimal
    reorder_qty: Decimal
    last_counted: date | None

    _sku = field_validator("sku", mode="before")(lambda v: c.normalize_item_no(v))
    _wh = field_validator("warehouse", mode="before")(lambda v: c.normalize_warehouse(v))
    _qoh = field_validator("qty_on_hand", mode="before")(
        lambda v: c.parse_quantity(v, field="qty on hand")
    )
    _rl = field_validator("reorder_level", mode="before")(
        lambda v: c.parse_quantity(v, field="reorder level")
    )
    _rq = field_validator("reorder_qty", mode="before")(
        lambda v: c.parse_quantity(v, field="reorder qty")
    )
    _lc = field_validator("last_counted", mode="before")(lambda v: c.parse_optional_date(v))

    @classmethod
    def from_row(cls, row: dict[str, str]) -> InventoryRecord:
        return cls(
            sku=row["ITEM_NO"], warehouse=row["WHSE"], qty_on_hand=row["QTY_ON_HAND"],
            reorder_level=row["REORDER_PT"], reorder_qty=row["REORDER_QTY"],
            last_counted=row["LAST_COUNT"],
        )  # fmt: skip


class _OrderLine(_Row):
    doc_number: str
    order_date: date
    party_code: str
    warehouse: str
    line_no: int
    sku: str
    quantity: Decimal
    unit_price: Decimal
    status: str
    due_date: date | None

    _date = field_validator("order_date", mode="before")(lambda v: c.parse_date(v))
    _wh = field_validator("warehouse", mode="before")(lambda v: c.normalize_warehouse(v))
    _sku = field_validator("sku", mode="before")(lambda v: c.normalize_item_no(v))
    _price = field_validator("unit_price", mode="before")(
        lambda v: c.parse_quantity(v, field="unit price")
    )
    _due = field_validator("due_date", mode="before")(lambda v: c.parse_optional_date(v))

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        q = c.parse_quantity(v)
        if q == 0:
            raise ValueError("zero quantity")
        return q


class POLineRecord(_OrderLine):
    _doc = field_validator("doc_number", mode="before")(lambda v: c.normalize_doc_no(v, "PO"))
    _status = field_validator("status", mode="before")(
        lambda v: c.map_status(v, c.PO_STATUS_MAP, field="PO status")
    )

    @classmethod
    def from_row(cls, row: dict[str, str]) -> POLineRecord:
        return cls(
            doc_number=row["PO_NO"], order_date=row["PO_DATE"], party_code=row["SUPP_NO"].strip(),
            warehouse=row["WHSE"], line_no=row["LINE_NO"], sku=row["ITEM_NO"], quantity=row["QTY"],
            unit_price=row["UNIT_PRICE"], status=row["STATUS"], due_date=row["EXPECTED_DATE"],
        )  # fmt: skip


class SOLineRecord(_OrderLine):
    _doc = field_validator("doc_number", mode="before")(lambda v: c.normalize_doc_no(v, "SO"))
    _status = field_validator("status", mode="before")(
        lambda v: c.map_status(v, c.SO_STATUS_MAP, field="SO status")
    )

    @classmethod
    def from_row(cls, row: dict[str, str]) -> SOLineRecord:
        return cls(
            doc_number=row["SO_NO"], order_date=row["ORDER_DATE"],
            party_code=row["CUST_NO"].strip(),
            warehouse=row["WHSE"], line_no=row["LINE_NO"], sku=row["ITEM_NO"], quantity=row["QTY"],
            unit_price=row["UNIT_PRICE"], status=row["STATUS"], due_date=row["REQ_DATE"],
        )  # fmt: skip
