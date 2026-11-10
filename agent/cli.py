"""Ask AdOps Copilot a question from the terminal.

  python -m agent.cli "Which campaigns are underpacing?"
  python -m agent.cli                      # interactive mode
  python -m agent.cli --model scripted "..."   # offline stand-in, no API key
"""

from __future__ import annotations

import argparse
import asyncio
import json

from dotenv import load_dotenv

from agent.agent import Agent
from agent.llm import GeminiLLM, ScriptedLLM


def make_llm(name: str):
    return ScriptedLLM() if name == "scripted" else GeminiLLM()


def show(r) -> None:
    print(f"\n{r.answer}\n")
    tools = ", ".join(t["name"] for t in r.tool_calls) or "none"
    print(f"[route={r.route} outcome={r.outcome} tools={tools} "
          f"tokens={r.input_tokens}+{r.output_tokens} latency={r.latency_ms}ms cost=${r.cost_usd}]")
    if r.escalation:
        print("[escalation] " + json.dumps(r.escalation))


async def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("question", nargs="*")
    p.add_argument("--model", choices=["gemini", "scripted"], default="gemini")
    a = p.parse_args()
    async with Agent(make_llm(a.model)) as agent:
        if a.question:
            show(await agent.ask(" ".join(a.question)))
            return
        print("AdOps Copilot (synthetic data). Empty line to quit.")
        while (q := input("> ").strip()):
            show(await agent.ask(q))


if __name__ == "__main__":
    asyncio.run(main())
