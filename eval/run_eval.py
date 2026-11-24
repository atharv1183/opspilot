"""Evaluate the OpsPilot agent on eval/queries.yaml and write eval/results.md.

Metrics (all computed from the actual run, nothing is estimated):
* tool-selection accuracy - expected tools are a subset of the tools the agent called
* answer correctness      - share of queries whose answer contains *all* expected key facts
                            (plus the mean share of facts found); optional LLM-as-judge (--judge)
* retrieval hit@3         - for RAG queries, an expected doc is among the top-3 retrieved chunks
* HITL compliance         - write queries paused for human approval before the PO was created
* latency                 - end-to-end agent time per query (mean / p50 / p95)

Usage::

    python eval/run_eval.py                       # all 50 queries, configured LLM provider
    python eval/run_eval.py --provider offline    # rule-based planner (no API key)
    python eval/run_eval.py --limit 5 --no-write  # CI smoke test
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

EVAL_DIR = ROOT / "eval"
RESULTS_MD = EVAL_DIR / "results.md"


def norm(text: str) -> str:
    """Lower-case and drop formatting noise so '$1,077' == '1077', 'PRICE_VARIANCE' == 'price variance'."""
    t = text.lower().replace(",", "").replace("$", "")
    t = re.sub(r"[_\-]", " ", t)
    return re.sub(r"\s+", " ", t)


def fact_found(fact: str | list[str], answer: str) -> bool:
    options = fact if isinstance(fact, list) else [fact]
    a = norm(answer)
    return any(norm(str(o)) in a for o in options)


def judge(question: str, facts: list, answer: str) -> tuple[bool | None, str]:
    """LLM-as-judge: is the answer correct given the reference facts?"""
    from opspilot.agent.llm import get_chat_model

    prompt = (
        "You grade answers of an ERP assistant. Reference key facts (ground truth): "
        f"{json.dumps(facts)}\nQuestion: {question}\nAnswer: {answer}\n\n"
        "Is the answer correct and consistent with the reference facts? Reply with JSON only: "
        '{"correct": true|false, "reason": "<short>"}'
    )
    try:
        raw = get_chat_model().invoke(prompt).content
        raw = raw if isinstance(raw, str) else str(raw)
        data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
        return bool(data.get("correct")), str(data.get("reason", ""))
    except Exception as exc:  # judge is optional; never fail the run
        return None, f"judge error: {exc}"


def pct(n: float, d: float) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


def run(args: argparse.Namespace) -> dict[str, Any]:
    from opspilot.agent.service import OpsPilotAgent
    from opspilot.db.session import get_sessionmaker, session_scope
    from opspilot.rag.retriever import search, unique_docs

    spec = yaml.safe_load((EVAL_DIR / "queries.yaml").read_text(encoding="utf-8"))["queries"]
    if args.ids:
        spec = [q for q in spec if q["id"] in set(args.ids.split(","))]
    if args.limit:
        spec = spec[: args.limit]

    if not args.no_reset:  # remove requisitions / POs created by earlier runs
        from opspilot.seed import run_seed

        with session_scope() as s:
            run_seed(s)

    agent = OpsPilotAgent(args.provider)
    print(f"Provider: {agent.provider_label} | {len(spec)} queries")
    # warm-up (embedding model load, WSDL fetch) so latency reflects steady state
    agent.chat("What is the price tolerance for the 3-way match?", "warmup")

    rows: list[dict[str, Any]] = []
    session = get_sessionmaker()()
    for i, q in enumerate(spec, 1):
        thread = f"eval-{q['id']}-{int(time.time())}"
        t0 = time.perf_counter()
        error = None
        approval_requested = False
        approval_before_write = True
        try:
            reply = agent.chat(q["query"], thread)
            guard = 0
            while reply.pending_approval and guard < 5:
                approval_requested = True
                executed = {r["name"] for r in reply.tool_results}
                if "create_purchase_order" in executed:
                    approval_before_write = False
                reply = agent.resume(thread, bool(q.get("approve", False)))
                guard += 1
            answer = reply.answer or ""
            called = [c["name"] for c in reply.tool_calls]
        except Exception as exc:  # record the failure, keep evaluating
            answer, called, error = "", [], f"{type(exc).__name__}: {exc}"
        latency = time.perf_counter() - t0

        expected = q.get("expected_tools", [])
        tool_ok = set(expected) <= set(called)
        facts = q.get("expected_facts", [])
        found = [fact_found(f, answer) for f in facts]
        row: dict[str, Any] = {
            "id": q["id"], "category": q["id"].split("-")[0], "query": q["query"],
            "expected_tools": expected, "called_tools": called, "tool_ok": tool_ok,
            "tool_exact": set(expected) == set(called), "facts_total": len(facts),
            "facts_found": sum(found), "answer_ok": all(found) if facts else True,
            "missing_facts": [f for f, ok in zip(facts, found, strict=True) if not ok],
            "latency_s": round(latency, 3), "answer": answer, "error": error,
        }  # fmt: skip
        if q.get("requires_approval"):
            row["hitl_ok"] = approval_requested and approval_before_write
        if q.get("expected_docs"):
            hits = search(session, q["query"], k=3)
            docs = unique_docs(hits)
            row["retrieved_docs"] = docs
            row["retrieval_hit3"] = any(d in docs for d in q["expected_docs"])
        if args.judge and agent.provider != "offline":
            row["judge_ok"], row["judge_reason"] = judge(q["query"], facts, answer)
        rows.append(row)
        flag = "OK " if (tool_ok and row["answer_ok"]) else "XX "
        print(f"[{i:>2}/{len(spec)}] {flag}{q['id']:<9} tools={'ok' if tool_ok else 'MISS'} "
              f"facts={row['facts_found']}/{row['facts_total']} {latency:5.2f}s")  # fmt: skip
    session.close()
    return {"provider": agent.provider_label, "timestamp": datetime.now(UTC).isoformat(),
            "rows": rows}  # fmt: skip


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    rows = result["rows"]
    n = len(rows)
    lat = sorted(r["latency_s"] for r in rows)
    rag = [r for r in rows if "retrieval_hit3" in r]
    # approval compliance is measured over queries where the agent actually attempted a PO
    hitl = [r for r in rows if "hitl_ok" in r and "create_purchase_order" in r["called_tools"]]
    judged = [r for r in rows if r.get("judge_ok") is not None]
    return {
        "n": n,
        "tool_acc": sum(r["tool_ok"] for r in rows) / n,
        "tool_exact": sum(r["tool_exact"] for r in rows) / n,
        "answer_acc": sum(r["answer_ok"] for r in rows) / n,
        "fact_recall": sum(r["facts_found"] for r in rows) / max(1, sum(r["facts_total"] for r in rows)),
        "hit3": (sum(r["retrieval_hit3"] for r in rag) / len(rag)) if rag else None,
        "n_rag": len(rag),
        "hitl": (sum(r["hitl_ok"] for r in hitl) / len(hitl)) if hitl else None,
        "n_hitl": len(hitl),
        "judge": (sum(r["judge_ok"] for r in judged) / len(judged)) if judged else None,
        "n_judged": len(judged),
        "lat_mean": statistics.mean(lat), "lat_p50": statistics.median(lat),
        "lat_p95": lat[min(n - 1, int(round(0.95 * (n - 1))))],
        "errors": sum(1 for r in rows if r["error"]),
    }  # fmt: skip


def render(result: dict[str, Any]) -> str:
    s = summarize(result)
    rows = result["rows"]
    judge_txt = f"{pct(s['judge'], 1)} (n={s['n_judged']})" if s["judge"] is not None else "not run"
    hit3_txt = pct(s["hit3"], 1) if s["hit3"] is not None else "n/a"
    hitl_txt = pct(s["hitl"], 1) if s["hitl"] is not None else "n/a"
    out = [
        f"## Run: `{result['provider']}`",
        "",
        f"_{s['n']} queries · {result['timestamp'][:16].replace('T', ' ')} UTC · "
        "generated by `eval/run_eval.py`_",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tool-selection accuracy (expected ⊆ called) | **{pct(s['tool_acc'], 1)}** |",
        f"| Tool-selection exact match | {pct(s['tool_exact'], 1)} |",
        f"| Answer correctness (all key facts present) | **{pct(s['answer_acc'], 1)}** |",
        f"| Key-fact recall | {pct(s['fact_recall'], 1)} |",
        f"| LLM-as-judge correctness | {judge_txt} |",
        f"| Retrieval hit@3 (RAG queries, n={s['n_rag']}) | **{hit3_txt}** |",
        f"| PO attempts paused for human approval (n={s['n_hitl']}) | {hitl_txt} |",
        f"| Latency mean / p50 / p95 | {s['lat_mean']:.2f}s / {s['lat_p50']:.2f}s / {s['lat_p95']:.2f}s |",
        f"| Errors | {s['errors']} |",
        "",
        "| Category | Queries | Tool acc. | Answer acc. |",
        "|---|---:|---:|---:|",
    ]
    cats: dict[str, list[dict]] = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
    for cat, rs in cats.items():
        out.append(f"| {cat} | {len(rs)} | {pct(sum(r['tool_ok'] for r in rs), len(rs))} | "
                   f"{pct(sum(r['answer_ok'] for r in rs), len(rs))} |")  # fmt: skip
    fails = [r for r in rows if not (r["tool_ok"] and r["answer_ok"]) or r["error"]]
    out += ["", f"<details><summary>Failures ({len(fails)})</summary>", ""]
    for r in fails:
        out.append(f"- **{r['id']}** - called `{r['called_tools']}`, expected `{r['expected_tools']}`; "
                   f"missing facts `{r['missing_facts']}`" + (f"; error: {r['error']}" if r["error"] else ""))  # fmt: skip
    out += ["", "</details>", ""]
    return "\n".join(out)


def write_results(result: dict[str, Any]) -> None:
    """Keep one section per provider in results.md (re-running a provider replaces its section)."""
    header = (
        "# OpsPilot evaluation results\n\n"
        "Real numbers from `python eval/run_eval.py` on the 50 queries in "
        "[`queries.yaml`](queries.yaml). Each section is one run; re-running a provider replaces "
        "its section. Raw per-query results (answers, tools, latencies) are in `eval/runs/`.\n\n"
        "Key facts are matched case-insensitively, ignoring `$`, `,`, `_` and `-`.\n"
    )
    sections: dict[str, str] = {}
    if RESULTS_MD.exists():
        for block in RESULTS_MD.read_text(encoding="utf-8").split("<!-- run:")[1:]:
            label, _, body = block.partition(" -->")
            sections[label.strip()] = body.split("<!-- end -->")[0].strip()
    sections[result["provider"]] = render(result)
    parts = [header]
    for label, body in sections.items():
        parts.append(f"<!-- run:{label} -->\n{body}\n<!-- end -->\n")
    RESULTS_MD.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Run the OpsPilot evaluation")
    p.add_argument("--provider", choices=["openai", "gemini", "offline"], default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--ids", default="", help="comma-separated query ids")
    p.add_argument("--judge", action="store_true", help="also grade with the LLM (LLM-as-judge)")
    p.add_argument("--no-write", action="store_true", help="don't update eval/results.md")
    p.add_argument("--no-reset", action="store_true", help="don't reset agent-created POs first")
    p.add_argument(
        "--render",
        nargs="+",
        metavar="RUN_JSON",
        help="re-render results.md from saved eval/runs/*.json without re-running",
    )
    args = p.parse_args()

    if args.render:
        for path in args.render:
            write_results(json.loads(Path(path).read_text(encoding="utf-8")))
        print(f"re-rendered {RESULTS_MD} from {len(args.render)} saved run(s)")
        return

    result = run(args)
    s = summarize(result)
    runs = EVAL_DIR / "runs"
    runs.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", result["provider"])
    (runs / f"{stamp}_{safe}.json").write_text(json.dumps(result, indent=2, default=str))
    print(f"\ntool acc {pct(s['tool_acc'], 1)} | answer acc {pct(s['answer_acc'], 1)} | "
          f"hit@3 {pct(s['hit3'], 1) if s['hit3'] is not None else 'n/a'} | "
          f"mean latency {s['lat_mean']:.2f}s | errors {s['errors']}")  # fmt: skip
    if not args.no_write:
        write_results(result)
        print(f"wrote {RESULTS_MD}")


if __name__ == "__main__":
    main()
