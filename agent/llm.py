"""Model adapters.

The agent never talks to a model API directly. It uses a small interface:

    session = llm.start(system_prompt, tools)
    turn = session.send_user(text)            -> ModelTurn
    turn = session.send_tool_results(results) -> ModelTurn

A ModelTurn is either a list of tool calls or a final text answer, plus token usage.
That makes the model swappable:
  - GeminiLLM:   the real model (needs GEMINI_API_KEY).
  - ScriptedLLM: a rule-based stand-in used only to test the plumbing offline (tests, CI).
                 It is NOT a model and its eval scores mean nothing about quality.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class ModelTurn:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolSpec:
    name: str
    description: str
    json_schema: dict


def _clean_schema(schema: Any) -> Any:
    """Drop JSON-schema keys some model APIs reject (titles, defaults)."""
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k not in ("title", "default")}
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


# ------------------------------------------------------------------ Gemini

class GeminiLLM:
    def __init__(self, model: str | None = None, temperature: float = 0.1):
        from google import genai  # imported here so tests don't need an API key
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        self.client = genai.Client(api_key=key)
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.temperature = temperature

    def start(self, system_prompt: str, tools: list[ToolSpec]) -> "GeminiSession":
        return GeminiSession(self, system_prompt, tools)


class GeminiSession:
    def __init__(self, llm: GeminiLLM, system_prompt: str, tools: list[ToolSpec]):
        from google.genai import types
        self.types = types
        self.llm = llm
        self.contents: list = []
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=llm.temperature,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters_json_schema=_clean_schema(t.json_schema))
                for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _generate(self) -> ModelTurn:
        resp = self.llm.client.models.generate_content(
            model=self.llm.model, contents=self.contents, config=self.config)
        content = resp.candidates[0].content
        # Keep the model's turn exactly as returned (it may carry thought signatures).
        self.contents.append(content)
        usage = resp.usage_metadata
        turn = ModelTurn(input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                         output_tokens=(getattr(usage, "candidates_token_count", 0) or 0)
                         + (getattr(usage, "thoughts_token_count", 0) or 0))
        texts = []
        for part in content.parts or []:
            if part.function_call:
                turn.tool_calls.append(ToolCall(part.function_call.name, dict(part.function_call.args or {})))
            elif part.text and not getattr(part, "thought", False):
                texts.append(part.text)
        if not turn.tool_calls:
            turn.text = "".join(texts).strip()
        return turn

    def send_user(self, text: str) -> ModelTurn:
        t = self.types
        self.contents.append(t.Content(role="user", parts=[t.Part(text=text)]))
        return self._generate()

    def send_tool_results(self, results: list[tuple[str, dict]]) -> ModelTurn:
        t = self.types
        parts = [t.Part(function_response=t.FunctionResponse(name=name, response={"result": result}))
                 for name, result in results]
        self.contents.append(t.Content(role="user", parts=parts))
        return self._generate()


# ------------------------------------------------------------------ Scripted stand-in

class ScriptedLLM:
    """Keyword-driven stand-in for offline tests. Picks plausible tools and writes a
    plain summary of what they returned. It exists only to exercise the agent loop."""

    def start(self, system_prompt: str, tools: list[ToolSpec]) -> "ScriptedSession":
        return ScriptedSession({t.name for t in tools})


class ScriptedSession:
    _CID = re.compile(r"\bC\d{3}\b", re.IGNORECASE)

    def __init__(self, tool_names: set[str]):
        self.tool_names = tool_names
        self.question = ""

    def _plan(self, q: str) -> list[ToolCall]:
        ql = q.lower()
        cid = self._CID.search(q)
        if "region" in ql or "country" in ql or "market" in ql:
            return []
        if any(w in ql for w in ("policy", "runbook", "sla", "escalat", "make-good", "make good",
                                  "define", "definition", "density", "what should")):
            return [ToolCall("search_runbooks", {"query": q})]
        if ("underpacing" in ql or "overpacing" in ql or "pacing issues" in ql) and not cid:
            return [ToolCall("list_pacing_issues", {})]
        if cid and "pacing" in ql:
            return [ToolCall("get_pacing", {"campaign_id": cid.group(0).upper()})]
        if cid and ("when" in ql or "daily" in ql or "trend" in ql):
            return [ToolCall("get_daily_delivery", {"campaign_id": cid.group(0).upper()})]
        if cid:
            return [ToolCall("get_campaign_performance", {"campaign_id": cid.group(0).upper()})]
        if "forecast" in ql or "inventory" in ql:
            for p in ("live_news_primetime", "live_sports", "vod_entertainment", "vod_news_clips"):
                if p.replace("_", " ") in ql or p in ql:
                    return [ToolCall("get_inventory_forecast", {"placement": p, "days": 7})]
        if "this week" in ql and "last week" in ql:
            return [ToolCall("get_dataset_info", {})]
        return [ToolCall("list_campaigns", {"status": "active"})]

    def send_user(self, text: str) -> ModelTurn:
        text = re.sub(r"^\[route hint:[^\]]*\]\n", "", text)
        self.question = text
        calls = [c for c in self._plan(text) if c.name in self.tool_names]
        if not calls:
            return ModelTurn(text="That information isn't in the dataset I can access.")
        return ModelTurn(tool_calls=calls)

    def send_tool_results(self, results: list[tuple[str, dict]]) -> ModelTurn:
        name, result = results[-1]
        if name == "get_dataset_info":
            w = result
            return ModelTurn(tool_calls=[ToolCall("compare_periods", {
                "metric": "revenue_inr", "period_a_start": w["last_week"]["start"],
                "period_a_end": w["last_week"]["end"], "period_b_start": w["this_week"]["start"],
                "period_b_end": w["this_week"]["end"]})])
        return ModelTurn(text=f"[scripted] {name} returned: {json.dumps(result)[:600]}")
