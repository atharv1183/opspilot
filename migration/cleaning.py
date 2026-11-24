"""Pure, unit-tested cleaning rules for legacy ERP data.

Each function either returns a normalised value or raises ``ValueError`` with a human-readable
reason. The reason strings end up in the reconciliation report, so keep them short and stable.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Legacy convention (documented by the old ERP team): "/" dates are US (MM/DD/YYYY),
# "." dates are European (DD.MM.YYYY).
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y", "%Y%m%d", "%b %d, %Y", "%d-%b-%Y")

UOM_MAP = {
    "EA": "EA", "EACH": "EA", "PCS": "EA", "PC": "EA", "UNIT": "EA", "PIECE": "EA",
    "BOX": "BOX", "BX": "BOX", "CTN": "BOX", "CARTON": "BOX",
    "KG": "KG", "KGS": "KG", "KILOGRAM": "KG", "KILO": "KG",
    "L": "L", "LTR": "L", "LITRE": "L", "LITER": "L",
    "M": "M", "MTR": "M", "METER": "M", "METRE": "M",
}  # fmt: skip

COUNTRY_MAP = {
    "USA": "United States", "US": "United States", "U.S.": "United States",
    "UNITED STATES": "United States", "CA": "Canada", "CAN": "Canada", "CANADA": "Canada",
    "MX": "Mexico", "MEXICO": "Mexico", "DE": "Germany", "GERMANY": "Germany",
    "DEUTSCHLAND": "Germany", "IN": "India", "INDIA": "India", "CN": "China", "CHINA": "China",
    "UK": "United Kingdom", "GB": "United Kingdom", "UNITED KINGDOM": "United Kingdom",
}  # fmt: skip

SUPPLIER_STATUS_MAP = {
    "A": "APPROVED", "ACTIVE": "APPROVED", "APPROVED": "APPROVED",
    "P": "PENDING", "PENDING": "PENDING", "NEW": "PENDING",
    "B": "BLOCKED", "BLOCKED": "BLOCKED", "INACTIVE": "BLOCKED",
}  # fmt: skip

PO_STATUS_MAP = {
    "CLOSED": "CLOSED", "CLSD": "CLOSED", "RECEIVED": "RECEIVED", "RECV": "RECEIVED",
    "PARTIAL": "PARTIALLY_RECEIVED", "PART RECV": "PARTIALLY_RECEIVED",
    "SENT": "SENT", "OPEN": "SENT", "APPROVED": "APPROVED", "RELEASED": "APPROVED",
    "CANCELLED": "CANCELLED", "CNX": "CANCELLED",
    "PENDING": "PENDING_APPROVAL", "AWAITING APPROVAL": "PENDING_APPROVAL",
}  # fmt: skip

SO_STATUS_MAP = {
    "CLOSED": "CLOSED", "COMPLETE": "CLOSED", "INVOICED": "INVOICED", "BILLED": "INVOICED",
    "SHIPPED": "SHIPPED", "PART SHIP": "PARTIALLY_SHIPPED", "PARTIAL": "PARTIALLY_SHIPPED",
    "ALLOCATED": "ALLOCATED", "ALLOC": "ALLOCATED", "OPEN": "OPEN", "NEW": "OPEN",
    "ENTERED": "OPEN", "CREDIT HOLD": "CREDIT_HOLD", "HOLD-CR": "CREDIT_HOLD",
    "CANCELLED": "CANCELLED", "CNX": "CANCELLED",
}  # fmt: skip

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def blank(value: str | None) -> bool:
    """True for None / empty / whitespace-only / common null tokens."""
    return value is None or value.strip().upper() in {"", "N/A", "NA", "NULL", "NONE", "-"}


def parse_date(value: str | None) -> date:
    """Parse any of the legacy date formats. Raises ValueError if unparseable."""
    if blank(value):
        raise ValueError("missing date")
    v = value.strip()  # type: ignore[union-attr]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date '{v}'")


def parse_optional_date(value: str | None) -> date | None:
    """Like :func:`parse_date` but returns None for blank/invalid values."""
    try:
        return parse_date(value)
    except ValueError:
        return None


def parse_decimal(value: str | None, *, field: str = "value") -> Decimal:
    """Parse '$1,234.50', ' 12 ', '12.0' -> Decimal. Raises ValueError if missing/invalid."""
    if blank(value):
        raise ValueError(f"missing {field}")
    v = value.strip().replace("$", "").replace(",", "")  # type: ignore[union-attr]
    try:
        return Decimal(v)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field} '{value.strip()}'") from exc  # type: ignore[union-attr]


def parse_quantity(value: str | None, *, field: str = "quantity") -> Decimal:
    """Parse a non-negative quantity."""
    q = parse_decimal(value, field=field)
    if q < 0:
        raise ValueError(f"negative {field} ({q})")
    return q


def normalize_uom(value: str | None) -> str:
    """Map free-text units ('each', 'kgs', 'carton') to the UnitOfMeasure enum."""
    if blank(value):
        raise ValueError("missing unit of measure")
    key = value.strip().rstrip(".").upper()  # type: ignore[union-attr]
    if key not in UOM_MAP:
        raise ValueError(f"unrecognised unit of measure '{value.strip()}'")  # type: ignore[union-attr]
    return UOM_MAP[key]


def normalize_item_no(value: str | None) -> str:
    """'p-1001', ' P-1001 ', 'P1001' -> 'ITM-1001'."""
    if blank(value):
        raise ValueError("missing item number")
    digits = re.sub(r"\D", "", value)  # type: ignore[arg-type]
    if not digits:
        raise ValueError(f"invalid item number '{value}'")
    return f"ITM-{int(digits)}"


def normalize_doc_no(value: str | None, prefix: str) -> str:
    """'po-10001', 'PO 10001', 'PO10001' -> 'PO-10001' (same for SO)."""
    if blank(value):
        raise ValueError(f"missing {prefix} number")
    digits = re.sub(r"\D", "", value)  # type: ignore[arg-type]
    if not digits:
        raise ValueError(f"invalid {prefix} number '{value}'")
    return f"{prefix}-{int(digits)}"


def normalize_warehouse(value: str | None) -> str:
    """'Whse B', 'wh_b', 'B', 'WAREHOUSE B' -> 'WH-B'."""
    if blank(value):
        raise ValueError("missing warehouse")
    v = value.strip().upper()  # type: ignore[union-attr]
    m = re.fullmatch(r"(?:WH[-_ ]?|WHSE\s+|WAREHOUSE\s+)?([A-Z])", v)
    if not m:
        raise ValueError(f"unrecognised warehouse '{value.strip()}'")  # type: ignore[union-attr]
    return f"WH-{m.group(1)}"


def normalize_country(value: str | None) -> str | None:
    """Canonical country name, or the trimmed original if unknown."""
    if blank(value):
        return None
    v = value.strip()  # type: ignore[union-attr]
    return COUNTRY_MAP.get(v.upper(), v)


def normalize_terms(value: str | None) -> str:
    """'Net 30', '30 days', 'N30' -> 'NET30' (default NET30)."""
    if blank(value):
        return "NET30"
    digits = re.sub(r"\D", "", value)  # type: ignore[arg-type]
    return f"NET{int(digits)}" if digits else "NET30"


def normalize_email(value: str | None) -> str | None:
    """Lower-case a valid e-mail; invalid or blank -> None."""
    if blank(value):
        return None
    v = value.strip().lower()  # type: ignore[union-attr]
    return v if _EMAIL_RE.match(v) else None


def normalize_tax_id(value: str | None) -> str | None:
    """'12-3456789' and '123456789' -> '12-3456789'."""
    if blank(value):
        return None
    digits = re.sub(r"\D", "", value)  # type: ignore[arg-type]
    if len(digits) != 9:
        return None
    return f"{digits[:2]}-{digits[2:]}"


def clean_name(value: str | None) -> str:
    """Collapse whitespace and strip trailing punctuation. Raises if blank."""
    if blank(value):
        raise ValueError("missing name")
    return re.sub(r"\s+", " ", value).strip().rstrip(".")  # type: ignore[arg-type]


def name_key(value: str) -> str:
    """Normalisation used as a de-duplication key (case, punctuation, '&' vs 'and').

    'Shaw, Price and White.' / 'SHAW PRICE & WHITE' -> 'shaw price and white'.
    Legal suffixes are kept on purpose so 'Smith LLC' and 'Smith Inc' stay distinct.
    """
    v = value.lower().replace("&", " and ")
    v = re.sub(r"[^a-z0-9 ]", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def map_status(value: str | None, mapping: dict[str, str], *, field: str = "status") -> str:
    """Map a legacy status code to the new enum value."""
    if blank(value):
        raise ValueError(f"missing {field}")
    key = value.strip().upper()  # type: ignore[union-attr]
    if key not in mapping:
        raise ValueError(f"unknown {field} '{value.strip()}'")  # type: ignore[union-attr]
    return mapping[key]


def parse_lead_time(value: str | None, default: int = 7) -> int:
    """'7', '7 days', '' -> int days."""
    if blank(value):
        return default
    digits = re.sub(r"\D", "", value)  # type: ignore[arg-type]
    return int(digits) if digits else default
