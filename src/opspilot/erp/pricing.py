"""Deterministic supplier catalogue + quote engine behind the simulated legacy Supplier Portal.

The ERP has no supplier price list table (pricing lives in the external portal), so the portal
derives quotes from a stable hash of (supplier, item). Same inputs always give the same quote,
which keeps demos, tests and evals reproducible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

SUPPLIERS_PER_ITEM = 4


def _h(*parts: str) -> int:
    return int(hashlib.sha256(":".join(parts).encode()).hexdigest()[:12], 16)


@dataclass(frozen=True)
class Quote:
    supplier_code: str
    supplier_name: str
    supplier_status: str
    item_sku: str
    quantity: Decimal
    unit_price: Decimal
    currency: str
    lead_time_days: int
    total: Decimal


def volume_discount(qty: Decimal) -> Decimal:
    """Tiered volume discount used by every supplier on the portal."""
    if qty >= 1000:
        return Decimal("0.07")
    if qty >= 500:
        return Decimal("0.05")
    if qty >= 100:
        return Decimal("0.02")
    return Decimal("0")


def catalog_supplier_codes(
    sku: str, all_supplier_codes: list[str], preferred_code: str | None
) -> list[str]:
    """Supplier codes that offer ``sku`` on the portal: preferred supplier + hashed picks."""
    codes = sorted(all_supplier_codes)
    picked: list[str] = [preferred_code] if preferred_code else []
    n = len(codes)
    salt = 0
    while len(picked) < SUPPLIERS_PER_ITEM and n:
        c = codes[_h(sku, str(salt)) % n]
        if c not in picked:
            picked.append(c)
        salt += 1
    return picked


def quote_unit_price(standard_cost: Decimal, supplier_code: str, sku: str, qty: Decimal) -> Decimal:
    """Supplier-specific unit price: cost x (0.88..1.12) x (1 - volume discount)."""
    factor = Decimal("0.88") + Decimal(_h(supplier_code, sku) % 25) / Decimal(100)
    price = standard_cost * factor * (Decimal(1) - volume_discount(qty))
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def quote_lead_time(base_lead_days: int, supplier_code: str, sku: str) -> int:
    """Supplier-specific lead time: base +/- up to 3 days (min 2)."""
    return max(2, base_lead_days + (_h(sku, supplier_code) % 7) - 3)
