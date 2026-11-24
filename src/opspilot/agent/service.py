"""High-level agent API used by FastAPI, the CLI and the eval harness."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from opspilot.agent.graph import build_graph, current_turn, make_llm_planner
from opspilot.agent.offline import offline_planner
from opspilot.config import get_settings


@dataclass
class AgentReply:
    thread_id: str
    answer: str | None
    tool_calls: list[dict[str, Any]]
    pending_approval: dict[str, Any] | None
    sources: list[str]
    provider: str
    latency_ms: int
    tool_results: list[dict[str, Any]] = field(default_factory=list)


class OpsPilotAgent:
    """Stateful agent (memory per ``thread_id``) with human-in-the-loop approvals."""

    def __init__(self, provider: str | None = None, checkpointer: Any = None) -> None:
        self.provider = provider or get_settings().resolved_llm_provider()
        if self.provider == "offline":
            planner = offline_planner
        else:
            from opspilot.agent.llm import get_chat_model

            planner = make_llm_planner(get_chat_model(self.provider))
        self.graph = build_graph(planner, checkpointer)

    @property
    def provider_label(self) -> str:
        s = get_settings()
        if self.provider == "openai":
            return f"openai:{s.openai_model}" + (
                f"@{s.openai_base_url}" if s.openai_base_url else ""
            )
        if self.provider == "gemini":
            return f"gemini:{s.gemini_model}"
        return "offline-rule-planner"

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    def chat(self, message: str, thread_id: str | None = None) -> AgentReply:
        """Send a user message. May return a pending approval instead of a final answer."""
        thread_id = thread_id or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        self.graph.invoke({"messages": [HumanMessage(message)]}, self._config(thread_id))
        return self._reply(thread_id, t0)

    def resume(self, thread_id: str, approve: bool) -> AgentReply:
        """Approve or reject the pending write action(s) of a thread and continue."""
        t0 = time.perf_counter()
        if not self.pending(thread_id):
            raise ValueError(f"Thread {thread_id} has no pending approval.")
        self.graph.invoke(Command(resume={"approved": approve}), self._config(thread_id))
        return self._reply(thread_id, t0)

    def pending(self, thread_id: str) -> dict[str, Any] | None:
        state = self.graph.get_state(self._config(thread_id))
        interrupts = getattr(state, "interrupts", ()) or ()
        return interrupts[0].value if interrupts else None

    def _reply(self, thread_id: str, t0: float) -> AgentReply:
        state = self.graph.get_state(self._config(thread_id))
        msgs = state.values.get("messages", [])
        turn = current_turn(msgs)
        calls = [{"name": tc["name"], "args": tc["args"]} for m in turn
                 if isinstance(m, AIMessage) for tc in m.tool_calls]  # fmt: skip
        results, sources = [], []
        for m in turn:
            if isinstance(m, ToolMessage) and isinstance(m.artifact, dict):
                results.append({"name": m.name, **m.artifact})
                sources += [s for s in m.artifact.get("sources", []) if s not in sources]
        pending = self.pending(thread_id)
        answer = None
        if not pending and turn and isinstance(turn[-1], AIMessage) and not turn[-1].tool_calls:
            answer = str(turn[-1].content)
        return AgentReply(thread_id, answer, calls, pending, sources, self.provider_label,
                          int((time.perf_counter() - t0) * 1000), results)  # fmt: skip


@lru_cache
def get_agent() -> OpsPilotAgent:
    """Process-wide agent instance (shared in-memory conversation store)."""
    return OpsPilotAgent()
