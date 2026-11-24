"""Agent endpoint: chat with OpsPilot, approve/reject pending write actions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from opspilot.agent.service import AgentReply, get_agent
from opspilot.api.schemas import ChatRequest, ChatResponse, ToolCallOut

router = APIRouter(prefix="/agent", tags=["agent"])


def _out(r: AgentReply) -> ChatResponse:
    return ChatResponse(
        thread_id=r.thread_id, answer=r.answer,
        tool_calls=[ToolCallOut(**c) for c in r.tool_calls],
        pending_approval=r.pending_approval, sources=r.sources, provider=r.provider,
        latency_ms=r.latency_ms, timestamp=datetime.now(UTC),
    )  # fmt: skip


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    """Ask a question / give an instruction.

    If the agent wants to perform a write action (e.g. create a PO), the response contains
    ``pending_approval`` and no answer. Send ``{"thread_id": ..., "approve": true|false}`` to
    continue.
    """
    agent = get_agent()
    if body.approve is not None:
        if not body.thread_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "thread_id is required")
        if not agent.pending(body.thread_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "No pending approval for this thread")
        return _out(agent.resume(body.thread_id, body.approve))
    if not body.message:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "message is required")
    if body.thread_id and agent.pending(body.thread_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This thread is waiting for approval; send approve=true/false first",
        )
    return _out(agent.chat(body.message, body.thread_id))
