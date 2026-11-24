"""Agent graph: routing, human-in-the-loop interrupt, rejection and conversation memory."""

from datetime import date

import pytest
from langchain_core.messages import AIMessage

from opspilot.agent.graph import build_graph
from opspilot.agent.offline import detect_intents, extract_entities, parse_date
from opspilot.agent.service import OpsPilotAgent


def test_parse_date_variants():
    today = date(2026, 11, 24)
    assert parse_date("can we ship by 2026-12-15?", today) == "2026-12-15"
    assert parse_date("deliver by Dec 20", today) == "2026-12-20"
    assert parse_date("deliver by Jan 10", today) == "2027-01-10"
    assert parse_date("in 10 days", today) == "2026-12-04"
    assert parse_date("no date here", today) is None


def test_entities_and_intents(migration_result):
    e = extract_entities("Can we promise 120 units of ITM-1088 from warehouse c by 2026-12-20?")
    assert (e.item, e.qty, e.warehouse, e.date) == ("ITM-1088", 120, "WH-C", "2026-12-20")
    assert detect_intents("Can we promise 120 units of ITM-1088 by 2026-12-20?", e) == ["atp"]
    e2 = extract_entities("Why is invoice INV-1042 on hold?")
    assert e2.inv == "INV-1042" and detect_intents("Why is invoice INV-1042 on hold?", e2) == [
        "invoice"
    ]
    q = "Which items are below reorder level in Warehouse B, and draft POs for them"
    assert detect_intents(q, extract_entities(q)) == ["replenish"]
    q = "Who approves a $30,000 purchase order?"
    assert detect_intents(q, extract_entities(q)) == ["sop"]


@pytest.fixture
def agent(soap_url):
    return OpsPilotAgent("offline")


def test_read_only_question_answers_without_approval(agent):
    r = agent.chat("How much ITM-1088 do we have in Dallas?")
    assert [c["name"] for c in r.tool_calls] == ["get_inventory_level"]
    assert r.pending_approval is None and "WH-B" in r.answer


def test_write_action_interrupts_then_resumes(agent):
    t = "t-approve"
    r = agent.chat("Which items are below reorder level in Warehouse B, and draft POs for them "
                   "from the cheapest approved supplier", t)  # fmt: skip
    assert r.answer is None
    actions = r.pending_approval["actions"]
    assert actions and all(a["tool"] == "create_purchase_order" for a in actions)
    assert "APPROVED" in actions[0]["description"]
    done = agent.resume(t, True)
    assert done.pending_approval is None
    assert done.answer.count("Created PO-") == len(actions)


def test_rejection_creates_nothing(agent):
    t = "t-reject"
    from opspilot.agent.tools import TOOL_BY_NAME

    pr = TOOL_BY_NAME["create_purchase_requisition"].invoke({"item": "ITM-1598", "qty": 40})
    r = agent.chat(f"Convert {pr['requisition_id']} to a PO with supplier SUP-0123", t)
    assert r.pending_approval
    done = agent.resume(t, False)
    assert "rejected" in done.answer and "Created PO-" not in done.answer
    with pytest.raises(ValueError):
        agent.resume(t, True)


def test_conversation_memory_follow_up(agent):
    t = "t-memory"
    first = agent.chat("Which items are below reorder level in Warehouse B?", t)
    assert first.tool_calls[0]["args"] == {"warehouse": "WH-B"}
    follow = agent.chat("What about warehouse D?", t)
    assert follow.tool_calls[0] == {"name": "find_low_stock_items", "args": {"warehouse": "WH-D"}}


def test_sop_answer_cites_document(agent):
    r = agent.chat("What is our policy on restocking fees for customer returns?")
    assert "returns-policy.md" in r.answer and r.sources[0] == "returns-policy.md"


def test_graph_with_stub_llm_planner(soap_url):
    """The graph works with any planner that emits tool calls (stands in for an LLM)."""
    calls = iter([
        AIMessage(content="", tool_calls=[{"name": "three_way_match", "args": {"invoice_id": "INV-1042"},
                                           "id": "c1", "type": "tool_call"}]),
        AIMessage(content="INV-1042 is on hold for a price variance."),
    ])  # fmt: skip
    graph = build_graph(lambda _msgs: next(calls))
    out = graph.invoke({"messages": [("user", "why is INV-1042 on hold?")]},
                       {"configurable": {"thread_id": "stub"}})  # fmt: skip
    assert out["messages"][-1].content.startswith("INV-1042 is on hold")
    assert out["messages"][2].artifact["match_status"] == "PRICE_VARIANCE"
