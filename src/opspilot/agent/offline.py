"""Deterministic, rule-based planner used when no LLM API key is configured.

It plays the same role as the LLM in the graph: given the conversation it emits an AIMessage
with tool calls (next step) or with the final answer. It exists so the whole system (graph,
tools, approval interrupt, API, eval harness) runs offline and in CI; it is intentionally
simple pattern matching, not "AI". Eval results always state which planner produced them.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from dateutil import parser as dparser
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

MAX_REPLENISH_ITEMS = 5
_WH_CITY = {"chicago": "WH-A", "dallas": "WH-B", "atlanta": "WH-C", "reno": "WH-D"}

RE_PO = re.compile(r"\bPO[-\s]?(\d{4,6})\b", re.I)
RE_SO = re.compile(r"\bSO[-\s]?(\d{4,6})\b", re.I)
RE_INV = re.compile(r"\bINV(?:OICE)?[-\s]?#?(\d{3,6})\b", re.I)
RE_PR = re.compile(r"\bPR[-\s]?(\d{3,6})\b", re.I)
RE_SUP = re.compile(r"\bSUP[-\s]?(\d{1,4})\b", re.I)
RE_ITM = re.compile(r"\bITM[-\s]?(\d{3,6})\b", re.I)
RE_WH = re.compile(r"\b(?:warehouse|whse|wh)[\s_-]*([a-d])\b", re.I)
RE_QTY_UNIT = re.compile(
    r"\b(\d[\d,]*(?:\.\d+)?)\s*(?:units?|pcs|pieces|ea|each|boxes|box|liters?|litres?|kg|kgs|"
    r"meters?|metres?|rolls?|pairs?)\b", re.I)  # fmt: skip
RE_QTY_VERB = re.compile(
    r"\b(?:qty|quantity|order|of|for|need|needs|want|wants|ship|deliver|promise|buy|request)\s+"
    r"(\d[\d,]*(?:\.\d+)?)\b", re.I)  # fmt: skip
RE_DATE = re.compile(
    r"\b(?:by|on|before|until|for)\s+(\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:,?\s+\d{4})?|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}(?:,?\s+\d{4})?)", re.I)  # fmt: skip

KW_PO = re.compile(r"\b(po|pos|purchase[- ]orders?)\b", re.I)
KW_LOW = re.compile(
    r"(below|under|at or below|beneath)\s+(the\s+|their\s+|its\s+)?re-?order|low[- ]stock|"
    r"running low|stock[- ]?outs?|re-?order exceptions|need(s)? (to be )?re-?order", re.I)  # fmt: skip
KW_REPLENISH = re.compile(r"\b(draft|create|raise|place|replenish|order them|reorder them)\b", re.I)
KW_ATP = re.compile(
    r"available[- ]to[- ]promise|\batp\b|can we (promise|deliver|ship|fulfil|fulfill|commit)|"
    r"(promise|deliver|ship)\b.{0,40}\bby\b", re.I)  # fmt: skip
KW_QUOTE = re.compile(r"\bquotes?\b|cheapest|best price|lowest price|supplier pric", re.I)
KW_INV_LEVEL = re.compile(
    r"how many|how much|on[- ]hand|in stock|stock level|inventory level|inventory (of|for)|"
    r"stock (of|for)|do we have|quantity of|stock position|available stock", re.I)  # fmt: skip
KW_CREATE_PR = re.compile(
    r"\b(create|raise|submit|open|make|draft)\b.{0,25}\brequisitions?\b", re.I
)
KW_POLICY = re.compile(
    r"\b(policy|policies|sop|procedure|tolerance|guideline|according to|allowed|rules?|"
    r"who (can )?approves?|how often|what should|next steps?|how do we resolve)\b", re.I)  # fmt: skip


@dataclass(frozen=True)
class Entities:
    po: str | None = None
    so: str | None = None
    inv: str | None = None
    pr: str | None = None
    supplier: str | None = None
    item: str | None = None
    warehouse: str | None = None
    qty: float | None = None
    date: str | None = None


@lru_cache(maxsize=1)
def _item_names() -> tuple[tuple[str, str], ...]:
    from opspilot.agent.tools import all_item_names

    return tuple(sorted(all_item_names(), key=lambda x: -len(x[1])))


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_date(text: str, today: date | None = None) -> str | None:
    """Extract a promise date ('by 2026-12-15', 'by Dec 20', 'next week', 'in 10 days')."""
    today = today or date.today()
    low = text.lower()
    if "tomorrow" in low:
        return (today + timedelta(days=1)).isoformat()
    if "next week" in low:
        return (today + timedelta(days=7)).isoformat()
    m = re.search(r"\bin\s+(\d{1,3})\s+days?\b", low)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    m = RE_DATE.search(text)
    if not m:
        return None
    raw = m.group(1)
    try:
        d = dparser.parse(raw, default=dparser.parse(today.isoformat())).date()
    except (ValueError, OverflowError):
        return None
    if d < today and not re.search(r"\d{4}", raw):
        d = d.replace(year=d.year + 1)
    return d.isoformat()


def extract_entities(text: str) -> Entities:
    """Pull IDs, item, warehouse, quantity and date out of a user message."""

    def grab(rx: re.Pattern[str], prefix: str, width: int = 0) -> str | None:
        m = rx.search(text)
        if not m:
            return None
        n = int(m.group(1))
        return f"{prefix}-{n:0{width}d}" if width else f"{prefix}-{n}"

    item = grab(RE_ITM, "ITM")
    if item is None:
        low = text.lower()
        for sku, name in _item_names():
            if name.lower() in low:
                item = sku
                break
    wh = None
    m = RE_WH.search(text)
    if m:
        wh = f"WH-{m.group(1).upper()}"
    else:
        for city, code in _WH_CITY.items():
            if city in text.lower():
                wh = code
    # quantity: strip IDs and dates first so their digits are not mistaken for quantities
    clean = re.sub(r"\b(?:PO|SO|INV|PR|SUP|ITM)[-\s]?\d+\b", " ", text, flags=re.I)
    clean = re.sub(r"\d{4}-\d{2}-\d{2}", " ", clean)
    qty = None
    m = RE_QTY_UNIT.search(clean) or RE_QTY_VERB.search(clean)
    if m:
        qty = _num(m.group(1))
    return Entities(
        po=grab(RE_PO, "PO"), so=grab(RE_SO, "SO"), inv=grab(RE_INV, "INV"),
        pr=grab(RE_PR, "PR"), supplier=grab(RE_SUP, "SUP", 4), item=item, warehouse=wh,
        qty=qty, date=parse_date(text),
    )  # fmt: skip


def detect_intents(text: str, e: Entities) -> list[str]:
    """Ordered list of intents found in the message."""
    intents: list[str] = []
    wants_po = bool(KW_PO.search(text))
    if e.inv:
        intents.append("invoice")
    if e.pr and (wants_po or re.search(r"\bconvert\b", text, re.I)):
        intents.append("convert_pr")
    elif KW_CREATE_PR.search(text) and not KW_LOW.search(text):
        intents.append("create_pr")
    if e.po:
        intents.append("po_status")
    if e.so:
        intents.append("so_status")
    if KW_LOW.search(text):
        intents.append("replenish" if (wants_po or KW_REPLENISH.search(text)) else "low_stock")
    if KW_ATP.search(text) and e.item:
        intents.append("atp")
    if KW_QUOTE.search(text) and e.item and "replenish" not in intents:
        intents.append("quote")
    if (KW_INV_LEVEL.search(text) and e.item
            and not {"atp", "quote", "create_pr"} & set(intents)):  # fmt: skip
        intents.append("inventory")
    if KW_POLICY.search(text) or not intents:
        intents.append("sop")
    return intents


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": {k: v for k, v in args.items() if v is not None},
            "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}  # fmt: skip


@dataclass
class _Done:
    name: str
    args: dict[str, Any]
    result: dict[str, Any]


def _turn(messages: list[BaseMessage]) -> tuple[str, list[_Done], list[str]]:
    """(current user text, tool results in this turn, previous user texts)."""
    idx = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    human = str(messages[idx].content)
    prev = [str(m.content) for m in messages[:idx] if isinstance(m, HumanMessage)]
    calls: dict[str, tuple[str, dict]] = {}
    done: list[_Done] = []
    for m in messages[idx + 1 :]:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls:
                calls[tc["id"]] = (tc["name"], tc["args"])
        elif isinstance(m, ToolMessage) and m.tool_call_id in calls:
            name, args = calls[m.tool_call_id]
            result = m.artifact if isinstance(m.artifact, dict) else {"summary": str(m.content)}
            done.append(_Done(name, args, result))
    return human, done, prev


def _first_steps(intent: str, e: Entities, text: str) -> list[dict[str, Any]]:
    if intent == "invoice":
        return [_call("three_way_match", invoice_id=e.inv)]
    if intent == "po_status":
        return [_call("get_po_status", po_id=e.po)]
    if intent == "so_status":
        return [_call("get_sales_order_status", so_id=e.so)]
    if intent in ("low_stock", "replenish"):
        return [_call("find_low_stock_items", warehouse=e.warehouse)]
    if intent == "atp":
        return [_call("check_available_to_promise", item=e.item, qty=e.qty or 1, date=e.date,
                      warehouse=e.warehouse)]  # fmt: skip
    if intent == "quote":
        return [_call("get_supplier_quote", item=e.item, qty=e.qty or 100)]
    if intent == "inventory":
        return [_call("get_inventory_level", item=e.item, warehouse=e.warehouse)]
    if intent == "create_pr":
        if not e.item:
            return []
        return [_call("create_purchase_requisition", item=e.item, qty=e.qty or 1,
                      warehouse=e.warehouse)]  # fmt: skip
    if intent == "convert_pr":
        if e.supplier:
            return [_call("create_purchase_order", requisition_id=e.pr, supplier_id=e.supplier)]
        req = _requisition(e.pr)
        if req is None:
            return [_call("create_purchase_order", requisition_id=e.pr, supplier_id="unknown")]
        return [_call("get_supplier_quote", item=req[0], qty=req[1])]
    if intent == "sop":
        return [_call("search_sop_docs", query=text)]
    return []


def _requisition(pr: str | None) -> tuple[str, float] | None:
    """(sku, qty) of a requisition - a read the planner needs to price a PR conversion."""
    if not pr:
        return None
    from opspilot.db.models import Item
    from opspilot.db.session import session_scope
    from opspilot.erp import ERPError
    from opspilot.erp.p2p import resolve_requisition

    try:
        with session_scope() as s:
            r = resolve_requisition(s, pr)
            item = s.get(Item, r.item_id)
            return (item.sku, float(r.quantity)) if item else None
    except ERPError:
        return None


def _follow_ups(intents: list[str], e: Entities, done: list[_Done], text: str
                ) -> list[dict[str, Any]]:  # fmt: skip
    """Next batch of calls for multi-step intents, given the results so far."""
    names = [d.name for d in done]
    wants_po = bool(KW_PO.search(text)) or "convert_pr" in intents
    if "replenish" in intents:
        low = next((d.result for d in done if d.name == "find_low_stock_items"), None)
        if low and "create_purchase_requisition" not in names:
            items = [x for x in low.get("items", []) if x["on_order"] < x["shortfall"]]
            calls: list[dict[str, Any]] = []
            for x in items[:MAX_REPLENISH_ITEMS]:
                calls.append(_call("create_purchase_requisition", item=x["sku"], qty=x["reorder_qty"],
                                   warehouse=x["warehouse"]))  # fmt: skip
                if wants_po:
                    calls.append(_call("get_supplier_quote", item=x["sku"], qty=x["reorder_qty"]))
            return calls
    if wants_po and "create_purchase_order" not in names:
        prs = [d.result for d in done if d.name == "create_purchase_requisition"
               and "requisition_id" in d.result]  # fmt: skip
        quotes = {d.result.get("item"): d.result for d in done if d.name == "get_supplier_quote"}
        if "convert_pr" in intents and not prs:
            req = _requisition(e.pr)
            if req and req[0] in quotes:
                prs = [{"requisition_id": e.pr, "item": req[0]}]
        calls = []
        for pr in prs:
            q = quotes.get(pr["item"])
            if q is None and "create_pr" in intents and "get_supplier_quote" not in names:
                calls.append(_call("get_supplier_quote", item=pr["item"], qty=pr["quantity"]))
                continue
            best = (q or {}).get("cheapest_approved")
            if best:
                calls.append(_call("create_purchase_order", requisition_id=pr["requisition_id"],
                                   supplier_id=best["supplier_code"]))  # fmt: skip
        return calls
    return []


def compose_answer(done: list[_Done]) -> str:
    """Final answer from tool summaries (+ citations for SOP searches)."""
    if not done:
        return ("I couldn't map that to an ERP action. Try e.g. 'Which items are below reorder "
                "level in Warehouse B?' or 'Why is invoice INV-1042 on hold?'.")  # fmt: skip
    parts, sources = [], []
    for d in done:
        parts.append(f"- {d.result.get('summary', '')}")
        sources += [s for s in d.result.get("sources", []) if s not in sources]
    low = next((d.result for d in done if d.name == "find_low_stock_items"), None)
    if low and any(d.name == "create_purchase_requisition" for d in done):
        covered = [x["sku"] for x in low.get("items", []) if x["on_order"] >= x["shortfall"]]
        if covered:
            parts.append(
                f"- Skipped {', '.join(covered)}: open purchase orders already cover the "
                "shortfall (replenishment policy SOP-INV-001)."
            )
    answer = "\n".join(parts)
    if sources:
        answer += "\n\nSources: " + ", ".join(f"[{s}]" for s in sources)
    return answer


def offline_planner(messages: list[BaseMessage]) -> AIMessage:
    """Planner entry point: returns the next tool calls or the final answer."""
    text, done, prev = _turn(messages)
    e = extract_entities(text)
    intents = detect_intents(text, e)
    # conversation memory: a short follow-up ("what about warehouse C?") reuses the last intent
    if intents == ["sop"] and prev and not KW_POLICY.search(text) and len(text.split()) <= 8:
        pe = extract_entities(prev[-1])
        merged = replace(pe, **{k: v for k, v in e.__dict__.items() if v is not None})
        prev_intents = detect_intents(prev[-1], merged)
        if prev_intents != ["sop"]:
            text, e, intents = prev[-1], merged, prev_intents
    if not done:
        calls = [c for i in intents for c in _first_steps(i, e, text)]
    else:
        calls = _follow_ups(intents, e, done, text)
        executed = {(d.name, tuple(sorted(d.args.items()))) for d in done}
        calls = [c for c in calls if (c["name"], tuple(sorted(c["args"].items()))) not in executed]
    if calls:
        return AIMessage(content="", tool_calls=calls)
    return AIMessage(content=compose_answer(done))
