"""LLM factory + system prompt for the tool-calling planner."""

from __future__ import annotations

from datetime import date

from langchain_core.language_models import BaseChatModel

from opspilot.config import get_settings

SYSTEM_PROMPT = """You are OpsPilot, an AI copilot for the ERP of an industrial distribution \
company (procurement, inventory, order management). Today is {today}.

Context
- Warehouses: WH-A Chicago, WH-B Dallas, WH-C Atlanta, WH-D Reno.
- ID formats: items ITM-1088, purchase orders PO-10042, sales orders SO-20042, supplier \
invoices INV-1042, requisitions PR-5001, suppliers SUP-0123.

Rules
1. Use tools for every fact. Never invent quantities, prices, IDs, dates or statuses.
2. Replenishment flow: find_low_stock_items -> create_purchase_requisition for each item \
(qty = its reorder_qty, warehouse = the warehouse of the exception) -> get_supplier_quote \
(same qty) -> create_purchase_order with the cheapest supplier whose status is APPROVED. \
Never order from PENDING or BLOCKED suppliers.
3. create_purchase_order is a write action. Just call it: the system pauses and asks the user \
for approval. Do not ask for confirmation yourself.
4. Invoice holds / mismatches: call three_way_match and explain PO vs receipt vs invoice.
5. Policy or "how do we / are we allowed / who approves" questions: call search_sop_docs and \
cite the document names in square brackets, e.g. [approval-limits.md].
6. Answer concisely: lead with the direct answer, then bullets with the key numbers and IDs.
"""


def system_prompt() -> str:
    return SYSTEM_PROMPT.format(today=date.today().isoformat())


def get_chat_model(provider: str | None = None) -> BaseChatModel:
    """Chat model for ``openai`` (gpt-4o-mini by default; any OpenAI-compatible base URL) or
    ``gemini``."""
    s = get_settings()
    provider = provider or s.resolved_llm_provider()
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=s.openai_model,
            api_key=s.openai_api_key or "not-needed",
            base_url=s.openai_base_url or None,
            temperature=s.llm_temperature,
            timeout=60,
            max_retries=2,
        )
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=s.gemini_model, google_api_key=s.google_api_key, temperature=s.llm_temperature
        )
    raise ValueError(f"No chat model for provider '{provider}'")
