"""The AdOps Copilot agent.

Flow for every question:
  1. Input guardrails (plain code): refuse, escalate, ask to clarify, or allow.
  2. Route: label the question as a 'data' or 'policy' question (logged, and given to the
     model as a hint).
  3. Tool loop: the model picks MCP tools, we run them, the model reads the results.
     Repeats until the model answers, escalates, or hits the step limit.
  4. Output guardrails: redact personal data, block claims of having taken actions.
  5. Trace: one JSON line per question in logs/traces.jsonl (route, tools, tokens,
     latency, cost, outcome). The evals and any monitoring read from these traces.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent.guardrails import check_input, check_output
from agent.llm import ToolSpec

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "traces.jsonl"

SYSTEM_PROMPT = """You are AdOps Copilot, a read-only assistant for an ad-operations team.

Rules:
- Use tools for every number. Never estimate or invent figures.
- Dates in the data are relative to the dataset date. Call get_dataset_info before answering
  anything about "today", "this week", or "last week".
- For process or policy questions, call search_runbooks and cite each source as
  [file > section]. Answer only from the retrieved text.
- You cannot change anything. If the user needs an action or a decision (pause, edit, approve,
  make-good), call escalate_to_human with a short summary instead of answering.
- If the data has no field for what is asked (for example region or country), say so plainly.
  Do not guess.
- If the question is ambiguous, ask one short clarifying question.
- Keep answers short: the answer first, then the 1-3 numbers that support it.
"""

ESCALATE_TOOL = ToolSpec(
    name="escalate_to_human",
    description="Hand the request to a human when it needs an action, a decision, or data you "
                "cannot access. Owner: ad_ops_oncall (serving, fill, zero delivery), account_owner "
                "(pacing, make-goods, advertiser matters), finance (billing, pricing).",
    json_schema={"type": "object", "properties": {
        "owner": {"type": "string", "enum": ["ad_ops_oncall", "account_owner", "finance"]},
        "reason": {"type": "string"},
        "summary": {"type": "string", "description": "What was asked and what you already found."}},
        "required": ["owner", "reason", "summary"]},
)

POLICY = re.compile(r"policy|runbook|\bsla\b|escalat|make-?good|defin|what does .* mean|"
                    r"how (do|should) (we|i)|what should|density|frequency cap|who handles|allowed",
                    re.IGNORECASE)

# USD per 1M tokens. Defaults are published Gemini 2.5 Flash list prices as of 2025;
# check current pricing and override with env vars.
PRICE_IN = float(os.environ.get("PRICE_IN_PER_M", "0.30"))
PRICE_OUT = float(os.environ.get("PRICE_OUT_PER_M", "2.50"))


@dataclass
class Result:
    question: str
    route: str
    outcome: str                      # answered | refused | escalated | clarified | failed
    answer: str
    tool_calls: list[dict] = field(default_factory=list)
    escalation: dict | None = None
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    model: str = ""


def route_of(question: str, guard_category: str) -> str:
    if guard_category in ("injection", "restricted_data"):
        return "blocked"
    if guard_category == "write_request":
        return "action_request"
    if guard_category == "vague":
        return "ambiguous"
    return "policy" if POLICY.search(question) else "data"


class Agent:
    def __init__(self, llm, max_steps: int = 6, log_path: Path | None = LOG_PATH):
        self.llm = llm
        self.max_steps = max_steps
        self.log_path = log_path
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tool_specs: list[ToolSpec] = []

    async def __aenter__(self) -> "Agent":
        params = StdioServerParameters(command=sys.executable, args=["-m", "server.mcp_server"],
                                       cwd=str(ROOT))
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        tools = (await self.session.list_tools()).tools
        self.tool_specs = [ToolSpec(t.name, t.description or "", t.input_schema) for t in tools]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def _call_mcp(self, name: str, args: dict) -> dict:
        res = await self.session.call_tool(name, args)
        text = "".join(getattr(c, "text", "") for c in res.content)
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {"text": text}
        if getattr(res, "is_error", False):
            payload = {"error": text or "tool error"}
        return payload

    async def ask(self, question: str) -> Result:
        t0 = time.perf_counter()
        decision = check_input(question)
        route = route_of(question, decision.category)
        r = Result(question=question, route=route, outcome="answered", answer="",
                   model=getattr(self.llm, "model", type(self.llm).__name__))

        if decision.action == "refuse":
            r.outcome, r.answer = "refused", decision.message
        elif decision.action == "clarify":
            r.outcome, r.answer = "clarified", decision.message
        elif decision.action == "escalate":
            r.outcome, r.answer = "escalated", decision.message
            r.escalation = {"owner": "account_owner", "reason": "action requested",
                            "summary": f"User asked: {question}", "source": "guardrail"}
        else:
            await self._run_loop(question, route, r)

        r.answer = check_output(r.answer)
        r.latency_ms = int((time.perf_counter() - t0) * 1000)
        r.cost_usd = round(r.input_tokens / 1e6 * PRICE_IN + r.output_tokens / 1e6 * PRICE_OUT, 6)
        self._log(r)
        return r

    async def _run_loop(self, question: str, route: str, r: Result) -> None:
        mcp_names = {t.name for t in self.tool_specs}
        session = self.llm.start(SYSTEM_PROMPT, self.tool_specs + [ESCALATE_TOOL])
        turn = session.send_user(f"[route hint: {route} question]\n{question}")
        for _ in range(self.max_steps):
            r.steps += 1
            r.input_tokens += turn.input_tokens
            r.output_tokens += turn.output_tokens
            if not turn.tool_calls:
                r.answer = turn.text or ""
                if re.search(r"\?\s*$", r.answer) and len(r.answer) < 300:
                    r.outcome = "clarified"
                return
            results = []
            for call in turn.tool_calls:
                if call.name == ESCALATE_TOOL.name:
                    r.escalation = {**call.args, "source": "model"}
                    r.tool_calls.append({"name": call.name, "args": call.args})
                    r.outcome = "escalated"
                    r.answer = (f"Escalated to {call.args.get('owner', 'a human')}: "
                                f"{call.args.get('reason', '')}. {call.args.get('summary', '')}").strip()
                    return
                if call.name not in mcp_names:
                    payload = {"error": f"Unknown tool {call.name}."}
                else:
                    payload = await self._call_mcp(call.name, call.args)
                r.tool_calls.append({"name": call.name, "args": call.args,
                                     "error": payload.get("error") if isinstance(payload, dict) else None})
                results.append((call.name, payload))
            turn = session.send_tool_results(results)
        r.outcome = "escalated"
        r.escalation = {"owner": "ad_ops_oncall", "reason": "step limit reached",
                        "summary": f"Could not finish: {question}", "source": "step_limit"}
        r.answer = "I couldn't complete this within my step limit, so I've handed it to a human."

    def _log(self, r: Result) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **asdict(r)}) + "\n")
