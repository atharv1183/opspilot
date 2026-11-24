"""3-way match logic (pure function) + the persisted seed scenarios."""

import pytest

from opspilot.db.enums import MatchStatus
from opspilot.erp.p2p import evaluate_three_way_match, three_way_match

PO = [
    {"id": 1, "line_no": 1, "sku": "ITM-1", "quantity": 100, "unit_price": 10.0},
    {"id": 2, "line_no": 2, "sku": "ITM-2", "quantity": 50, "unit_price": 20.0},
]
RECEIVED = {1: 100.0, 2: 50.0}


def inv(*lines):
    return [{"po_line_id": i, "quantity": q, "unit_price": p} for i, q, p in lines]


def test_exact_match():
    r = evaluate_three_way_match(PO, RECEIVED, inv((1, 100, 10.0), (2, 50, 20.0)))
    assert r.status == MatchStatus.MATCHED
    assert "Release for payment" in r.recommended_action


def test_price_within_tolerance_is_matched():
    r = evaluate_three_way_match(PO, RECEIVED, inv((1, 100, 10.19)))  # +1.9%
    assert r.status == MatchStatus.MATCHED


def test_price_variance_above_tolerance():
    r = evaluate_three_way_match(PO, RECEIVED, inv((1, 100, 10.65)))  # +6.5%
    assert r.status == MatchStatus.PRICE_VARIANCE
    assert r.lines[0].price_variance_pct == pytest.approx(6.5)
    assert "+6.5%" in r.explanation and "credit note" in r.recommended_action


def test_underbilling_is_not_a_hold():
    r = evaluate_three_way_match(PO, RECEIVED, inv((1, 100, 9.0)))
    assert r.status == MatchStatus.MATCHED


def test_quantity_billed_above_received():
    r = evaluate_three_way_match(PO, {1: 80.0}, inv((1, 100, 10.0)))
    assert r.status == MatchStatus.QTY_VARIANCE
    assert "billed qty 100 exceeds received qty 80" in r.explanation


def test_no_receipt_has_highest_precedence():
    r = evaluate_three_way_match(PO, {}, inv((1, 100, 12.0)))
    assert r.status == MatchStatus.NO_RECEIPT
    assert len(r.lines[0].issues) == 2  # no GRN + price variance both explained


def test_custom_tolerance():
    r = evaluate_three_way_match(PO, RECEIVED, inv((1, 100, 10.4)), price_tolerance_pct=5)
    assert r.status == MatchStatus.MATCHED


def test_seeded_invoice_scenarios(db):
    assert three_way_match(db, "INV-1042")["match_status"] == "PRICE_VARIANCE"
    assert three_way_match(db, "INV-1042")["invoice_status"] == "ON_HOLD"
    assert three_way_match(db, "INV-1017")["match_status"] == "QTY_VARIANCE"
    assert three_way_match(db, "INV-1088")["match_status"] == "NO_RECEIPT"
    assert three_way_match(db, "INV-1050")["match_status"] == "MATCHED"  # +1.2%, within tolerance
