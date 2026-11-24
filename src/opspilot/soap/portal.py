"""Business logic of the simulated Supplier Portal (kept separate from the SOAP plumbing)."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from sqlalchemy.orm import Session

from opspilot.db.enums import POStatus
from opspilot.erp.lookup import resolve_po
from opspilot.erp.p2p import supplier_quotes

CARRIERS = ["UPS Freight", "FedEx Freight", "DHL Supply Chain", "Old Dominion"]


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def portal_quotes(session: Session, item_id: str, qty: float) -> list[dict]:
    """All supplier quotes for an item, cheapest first (portal view, includes non-approved)."""
    return [
        {
            "supplier_code": q.supplier_code,
            "supplier_name": q.supplier_name,
            "supplier_status": q.supplier_status,
            "item_sku": q.item_sku,
            "quantity": float(q.quantity),
            "unit_price": float(q.unit_price),
            "currency": q.currency,
            "lead_time_days": q.lead_time_days,
            "total": float(q.total),
        }
        for q in supplier_quotes(session, item_id, qty)
    ]


def delivery_status(session: Session, po_id: str, today: date | None = None) -> dict:
    """Supplier-side shipping status for a PO, as the carrier/supplier portal reports it."""
    today = today or date.today()
    po = resolve_po(session, po_id)
    h = _h(po.po_number)
    carrier = CARRIERS[h % len(CARRIERS)]
    tracking = f"{carrier.split()[0][:3].upper()}-{h % 10**8:08d}"
    eta = po.expected_date or (po.order_date + timedelta(days=14))
    delay = 0
    if po.status in (POStatus.RECEIVED, POStatus.CLOSED):
        status, event = "DELIVERED", "Delivered to receiving dock and signed for."
    elif po.status == POStatus.PARTIALLY_RECEIVED:
        status, event = "PARTIALLY_DELIVERED", "Partial delivery made; remaining lines in transit."
    elif po.status == POStatus.SENT:
        delay = (h // 7) % 6 if h % 3 == 0 else 0
        eta = eta + timedelta(days=delay)
        if delay:
            status, event = "DELAYED", f"Carrier reports a {delay}-day delay (capacity issue)."
        elif eta - today <= timedelta(days=7):
            status, event = "IN_TRANSIT", "Shipment picked up by carrier."
        else:
            status, event = "CONFIRMED", "Order acknowledged by supplier, scheduled for dispatch."
    elif po.status == POStatus.APPROVED:
        status, event = "AWAITING_TRANSMISSION", "PO approved in ERP but not yet sent to supplier."
        carrier, tracking = "", ""
    elif po.status == POStatus.CANCELLED:
        status, event, carrier, tracking = "CANCELLED", "PO cancelled.", "", ""
    else:
        status, event, carrier, tracking = "NOT_SENT", "PO awaiting internal approval.", "", ""
    return {
        "po_number": po.po_number,
        "status": status,
        "carrier": carrier,
        "tracking_number": tracking,
        "eta": eta.isoformat(),
        "delay_days": delay,
        "last_event": event,
    }
