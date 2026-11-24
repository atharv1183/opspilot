"""Interactive terminal chat with OpsPilot: ``python -m opspilot.chat``.

Talks to the agent in-process (same graph as the API). Write actions pause for a y/N approval.
"""

from __future__ import annotations

import argparse
import uuid

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from opspilot.agent.service import AgentReply, OpsPilotAgent

EXAMPLES = [
    "Which items are below reorder level in Warehouse B, and draft POs for them from the "
    "cheapest approved supplier",
    "Why is invoice INV-1042 on hold?",
    "Can we promise 120 units of ITM-1088 by 2026-12-20?",
    "Who needs to approve a purchase order of $30,000?",
]


def _render(console: Console, r: AgentReply) -> None:
    if r.tool_calls:
        calls = ", ".join(f"{c['name']}({', '.join(f'{k}={v}' for k, v in c['args'].items())})"
                          for c in r.tool_calls)  # fmt: skip
        console.print(f"[dim]tools: {calls}[/dim]")
    if r.answer:
        console.print(Panel(Markdown(r.answer), title="OpsPilot", border_style="cyan"))
    console.print(f"[dim]{r.provider} · {r.latency_ms} ms · thread {r.thread_id}[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["openai", "gemini", "offline"], default=None)
    parser.add_argument("-q", "--query", help="ask one question and exit (auto-rejects writes)")
    args = parser.parse_args()
    console = Console()
    agent = OpsPilotAgent(args.provider)
    thread = uuid.uuid4().hex[:12]
    if args.query:
        r = agent.chat(args.query, thread)
        _render(console, r)
        return
    console.print(Panel("OpsPilot - ERP & supply-chain copilot. Type 'exit' to quit.\n\nTry:\n"
                        + "\n".join(f"  • {e}" for e in EXAMPLES), border_style="cyan"))  # fmt: skip
    while True:
        try:
            msg = console.input("[bold green]you>[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in {"exit", "quit", ":q"}:
            break
        if not msg:
            continue
        r = agent.chat(msg, thread)
        while r.pending_approval:
            _render(console, r)
            console.print(Panel("\n".join(f"• {a['description']}"
                                          for a in r.pending_approval["actions"]),
                                title="Approval required", border_style="yellow"))  # fmt: skip
            ok = console.input("[bold yellow]approve? [y/N][/bold yellow] ").strip().lower()
            r = agent.resume(thread, ok in {"y", "yes"})
        _render(console, r)


if __name__ == "__main__":
    main()
