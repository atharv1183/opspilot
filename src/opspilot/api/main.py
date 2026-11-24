"""FastAPI application: ERP REST API + agent endpoint. OpenAPI docs at /docs."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from opspilot import __version__
from opspilot.api.routers import agent, inventory, invoices, items, purchasing, sales
from opspilot.config import get_settings
from opspilot.db.session import get_engine
from opspilot.erp import ERPError

app = FastAPI(
    title="OpsPilot ERP API",
    version=__version__,
    description=(
        "Mock ERP (procurement, inventory, order management) with an agentic AI copilot.\n\n"
        "* **Procure-to-Pay**: requisition -> PO -> goods receipt -> supplier invoice (3-way match)\n"
        "* **Order-to-Cash**: sales order -> shipment -> customer invoice\n"
        "* **Integrations**: REST (this API) + SOAP legacy Supplier Portal (via zeep)\n"
        "* **Agent**: `POST /agent/chat` (LangGraph, human approval for write actions)"
    ),
)


@app.exception_handler(ERPError)
async def erp_error_handler(_: Request, exc: ERPError) -> JSONResponse:
    """Business-rule violations -> 422, missing records -> 404."""
    msg = str(exc).lower()
    code = 404 if ("not found" in msg or "no item matches" in msg) else 422
    return JSONResponse(status_code=code, content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness + DB connectivity."""
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__,
            "llm_provider": get_settings().resolved_llm_provider()}  # fmt: skip


for r in (items, inventory, purchasing, sales, invoices, agent):
    app.include_router(r.router)
