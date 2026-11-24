"""LangGraph agent: planner -> (human approval) -> tools -> planner ... -> answer.

```
START -> planner --tool calls (read)--> tools --> planner
                 --tool calls (write)-> human_approval --approved--> tools
                                                       --rejected--> planner
                 --no tool calls------> answer -> END
```

* ``planner`` is either a tool-calling LLM (OpenAI / Gemini) or the offline rule-based planner.
* ``human_approval`` calls ``interrupt()`` so the run pauses until a human approves or rejects
  every write action (e.g. ``create_purchase_order``). Resume with ``Command(resume=...)``.
* A checkpointer (in-memory by default) keyed by ``thread_id`` gives conversation memory.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from opspilot.agent.tools import TOOL_BY_NAME, TOOLS, WRITE_TOOLS, preview_write_action

Planner = Callable[[list[BaseMessage]], AIMessage]
MAX_TOOL_ROUNDS = 8
MAX_TOOL_RESULT_CHARS = 6000


def current_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Messages after (and excluding) the latest HumanMessage."""
    idx = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return messages[idx + 1 :]


def make_llm_planner(model: Any) -> Planner:
    """Wrap a chat model with the tools bound and the system prompt prepended."""
    from opspilot.agent.llm import system_prompt

    bound = model.bind_tools(TOOLS)

    def planner(messages: list[BaseMessage]) -> AIMessage:
        rounds = sum(1 for m in current_turn(messages) if isinstance(m, AIMessage) and m.tool_calls)
        history = [SystemMessage(system_prompt()), *messages[-40:]]
        while len(history) > 1 and isinstance(
            history[1], ToolMessage
        ):  # never start with orphan results
            history.pop(1)
        if rounds >= MAX_TOOL_ROUNDS:
            history.append(SystemMessage("Tool budget exhausted: answer now with what you have."))
            return model.invoke(history)
        return bound.invoke(history)

    return planner


def _execute(call: dict[str, Any]) -> ToolMessage:
    tool = TOOL_BY_NAME.get(call["name"])
    if tool is None:
        result: dict[str, Any] = {"error": f"Unknown tool {call['name']}",
                                  "summary": f"Unknown tool {call['name']}"}  # fmt: skip
    else:
        try:
            result = tool.invoke(call["args"])
        except Exception as exc:  # validation errors from bad LLM arguments etc.
            result = {"error": str(exc), "summary": f"Tool {call['name']} failed: {exc}"}
    content = json.dumps(result, default=str)
    if len(content) > MAX_TOOL_RESULT_CHARS:
        content = json.dumps({"summary": result.get("summary", ""), "truncated": True})
    return ToolMessage(content=content, tool_call_id=call["id"], name=call["name"], artifact=result)


def build_graph(planner: Planner, checkpointer: BaseCheckpointSaver | None = None
                ) -> CompiledStateGraph:  # fmt: skip
    """Compile the agent graph with the given planner."""

    def planner_node(state: MessagesState) -> dict:
        return {"messages": [planner(state["messages"])]}

    def route(state: MessagesState) -> Literal["tools", "human_approval", "answer"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            if any(tc["name"] in WRITE_TOOLS for tc in last.tool_calls):
                return "human_approval"
            return "tools"
        return "answer"

    def tools_node(state: MessagesState) -> dict:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        return {"messages": [_execute(tc) for tc in last.tool_calls]}

    def approval_node(state: MessagesState) -> Command[Literal["tools", "planner"]]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        writes = [tc for tc in last.tool_calls if tc["name"] in WRITE_TOOLS]
        decision = interrupt({
            "type": "approval_required",
            "message": "The agent wants to perform write action(s). Approve?",
            "actions": [{"id": tc["id"], "tool": tc["name"], "args": tc["args"],
                         "description": preview_write_action(tc["name"], tc["args"])}
                        for tc in writes],
        })  # fmt: skip
        approved = decision is True or (isinstance(decision, dict) and decision.get("approved"))
        if approved:
            return Command(goto="tools")
        # Rejected: answer every pending call so the transcript stays valid, run reads anyway.
        msgs: list[BaseMessage] = []
        for tc in last.tool_calls:
            if tc["name"] in WRITE_TOOLS:
                result = {"status": "rejected",
                          "summary": f"User rejected {tc['name']}({tc['args']}); nothing was created."}  # fmt: skip
                msgs.append(ToolMessage(content=json.dumps(result), tool_call_id=tc["id"],
                                        name=tc["name"], artifact=result))  # fmt: skip
            else:
                msgs.append(_execute(tc))
        return Command(goto="planner", update={"messages": msgs})

    def answer_node(state: MessagesState) -> dict:
        last = state["messages"][-1]
        sources: list[str] = []
        for m in current_turn(state["messages"]):
            if (
                isinstance(m, ToolMessage)
                and m.name == "search_sop_docs"
                and isinstance(m.artifact, dict)
            ):
                sources += [s for s in m.artifact.get("sources", []) if s not in sources]
        text = last.content if isinstance(last.content, str) else _flatten(last.content)
        missing = [s for s in sources if s not in text]
        if sources and len(missing) == len(sources):
            text += "\n\nSources: " + ", ".join(f"[{s}]" for s in sources)
        return {"messages": [AIMessage(content=text, id=last.id)]}

    g = StateGraph(MessagesState)
    g.add_node("planner", planner_node)
    g.add_node("tools", tools_node)
    g.add_node("human_approval", approval_node)
    g.add_node("answer", answer_node)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", route)
    g.add_edge("tools", "planner")
    g.add_edge("answer", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def _flatten(content: Any) -> str:
    """Gemini may return a list of content parts."""
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)
