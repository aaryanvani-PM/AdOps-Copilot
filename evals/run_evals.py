"""Run the eval set against the agent and score it.

  python -m evals.run_evals                 # real model (needs GEMINI_API_KEY)
  python -m evals.run_evals --model scripted  # offline plumbing check only

Scores per case:
  outcome_ok  the agent answered / escalated / refused / clarified as expected
  tool_ok     it called at least one expected tool (or no data tool, for cases that need none)
  answer_ok   the answer contains the expected numbers, IDs, dates, sources, or keywords
  pass        all three

Results are written to evals/results/<timestamp>.json and a readable summary .md.
All checks are deterministic string and number matches, so they can be audited.
Spot-check failures by hand: a strict check can fail a correct answer phrased differently.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from agent.agent import Agent
from agent.cli import make_llm

HERE = Path(__file__).parent
DATA_TOOLS = {"list_campaigns", "get_campaign_performance", "get_pacing", "list_pacing_issues",
              "get_daily_delivery", "compare_periods", "get_inventory_forecast", "search_runbooks"}
NUM = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(million|mn|m\b|lakh|lakhs|crore|k\b)?", re.IGNORECASE)
SCALE = {"million": 1e6, "mn": 1e6, "m": 1e6, "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "k": 1e3}


def numbers_in(text: str) -> list[float]:
    out = []
    for raw, unit in NUM.findall(text):
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        out.append(v * SCALE.get((unit or "").lower(), 1))
    return out


def number_found(expected: float, found: list[float]) -> bool:
    for v in found:
        for cand in (v, v * 100, v / 100):
            if abs(expected) >= 1000:
                if abs(cand - expected) <= abs(expected) * 0.015:
                    return True
            elif abs(cand - expected) <= max(0.6, abs(expected) * 0.01):
                return True
    return False


def check_answer(answer: str, checks: dict) -> tuple[bool, list[str]]:
    misses = []
    low = answer.lower()
    found = numbers_in(answer)
    for n in checks.get("numbers", []):
        if not number_found(float(n), found):
            misses.append(f"number {n}")
    for cid in checks.get("ids", []):
        if cid.lower() not in low:
            misses.append(f"id {cid}")
    for variants in checks.get("date_variants", []):
        if not any(v.lower() in low for v in variants):
            misses.append(f"date {variants[0]}")
    for src in checks.get("sources", []):
        if src.lower() not in low and src.replace(".md", "").replace("_", " ").lower() not in low:
            misses.append(f"source {src}")
    for kw in checks.get("keywords", []):
        if kw.lower() not in low:
            misses.append(f"keyword '{kw}'")
    if checks.get("keywords_any") and not any(k.lower() in low for k in checks["keywords_any"]):
        misses.append("says data unavailable")
    return not misses, misses


def score(case: dict, r) -> dict:
    called = {t["name"] for t in r.tool_calls}
    outcome_ok = r.outcome == case["expected_outcome"]
    if case["expected_tools"]:
        tool_ok = bool(called & set(case["expected_tools"]))
    else:
        tool_ok = not (called & DATA_TOOLS)
    if case["expected_outcome"] == "answered":
        answer_ok, misses = check_answer(r.answer, case["checks"])
    else:
        answer_ok, misses = True, []
    return {"id": case["id"], "category": case["category"], "question": case["question"],
            "expected_outcome": case["expected_outcome"], "outcome": r.outcome,
            "tools": sorted(called), "outcome_ok": outcome_ok, "tool_ok": tool_ok,
            "answer_ok": answer_ok, "misses": misses,
            "pass": outcome_ok and tool_ok and answer_ok,
            "answer": r.answer, "latency_ms": r.latency_ms, "cost_usd": r.cost_usd,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}


def summarize(rows: list[dict], model: str) -> dict:
    by_cat = defaultdict(list)
    for x in rows:
        by_cat[x["category"]].append(x)
    guard = [x for x in rows if x["category"] in ("action", "injection", "restricted", "ambiguous")]
    answered = [x for x in rows if x["expected_outcome"] == "answered"]
    lat = [x["latency_ms"] for x in rows if x["tools"] or x["outcome"] == "answered"] or [0]
    rate = lambda xs, k: round(sum(1 for x in xs if x[k]) / len(xs) * 100, 1) if xs else None
    return {
        "model": model,
        "cases": len(rows),
        "pass_rate_pct": rate(rows, "pass"),
        "answer_accuracy_pct": rate(answered, "answer_ok"),
        "tool_selection_pct": rate(rows, "tool_ok"),
        "correct_refusal_escalation_clarify_pct": rate(guard, "outcome_ok"),
        "by_category_pass_pct": {k: rate(v, "pass") for k, v in sorted(by_cat.items())},
        "latency_ms_median": int(statistics.median(lat)),
        "latency_ms_p90": int(sorted(lat)[max(0, int(len(lat) * 0.9) - 1)]),
        "avg_cost_usd_per_query": round(sum(x["cost_usd"] for x in rows) / len(rows), 6),
        "total_cost_usd": round(sum(x["cost_usd"] for x in rows), 6),
        "avg_tokens_per_query": int(sum(x["input_tokens"] + x["output_tokens"] for x in rows) / len(rows)),
    }


def write_report(summary: dict, rows: list[dict]) -> Path:
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out / f"{stamp}-{summary['model']}.json"
    path.write_text(json.dumps({"summary": summary, "cases": rows}, indent=2))
    lines = [f"# Eval results: {summary['model']} ({stamp} UTC)", "",
             "| Metric | Value |", "|---|---|"]
    for k, v in summary.items():
        if k != "by_category_pass_pct":
            lines.append(f"| {k} | {v} |")
    lines += ["", "| Category | Pass % |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in summary["by_category_pass_pct"].items()]
    lines += ["", "## Failures", ""]
    for x in rows:
        if not x["pass"]:
            lines.append(f"- **{x['id']}** {x['question']} → outcome={x['outcome']} "
                         f"tools={x['tools']} misses={x['misses']}")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return path


async def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=["gemini", "scripted"], default="gemini")
    p.add_argument("--only", help="comma-separated case IDs to run")
    a = p.parse_args()
    cases = [json.loads(l) for l in (HERE / "eval_set.jsonl").read_text().splitlines() if l.strip()]
    if a.only:
        keep = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    llm = make_llm(a.model)
    rows = []
    async with Agent(llm) as agent:
        for c in cases:
            r = await agent.ask(c["question"])
            row = score(c, r)
            rows.append(row)
            print(f"{'PASS' if row['pass'] else 'FAIL'} {c['id']:<4} {c['question'][:60]:<60} "
                  f"{row['outcome']:<10} {','.join(row['tools'])[:40]}")
    model_name = getattr(llm, "model", a.model)
    summary = summarize(rows, model_name)
    path = write_report(summary, rows)
    print("\n" + json.dumps(summary, indent=2))
    print(f"\nreport: {path.relative_to(HERE.parent)} (+ .md)")


if __name__ == "__main__":
    asyncio.run(main())
