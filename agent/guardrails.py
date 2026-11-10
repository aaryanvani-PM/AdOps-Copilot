"""Guardrails that run in plain code, before and after the model.

Input guardrails decide, before the model sees a question, whether it should be:
  - refused   (prompt injection, restricted data such as contract pricing or personal data)
  - escalated (the user wants an action taken; the copilot is read-only)
  - clarified (too vague to answer without guessing)
  - allowed

Output guardrails clean the model's answer:
  - redact anything that looks like an email address or phone number
  - block claims of having taken an action ("I have paused...")

These rules are deliberately simple and auditable. They are a safety net under the
model's own instructions, not a replacement for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|your\s+|the\s+|previous\s+|prior\s+)*"
    r"(instructions|rules|guardrails|policies)"
    r"|system\s+prompt|reveal\s+(your\s+)?(instructions|prompt)|developer\s+mode|jailbreak"
    r"|act\s+as\s+(an?\s+)?(admin|administrator|root)",
    re.IGNORECASE)

ACTION_VERBS = r"(pause|resume|stop|start|launch|create|delete|remove|edit|change|update|increase|" \
               r"decrease|raise|lower|set|approve|extend|cancel|reallocate|move)"
ACTION_OBJECTS = r"(campaign|line\s*item|budget|goal|bid|floor|cap|creative|targeting|make-?good|C\d{3})"
WRITE_INTENT = re.compile(rf"\b{ACTION_VERBS}\b[^.?!]{{0,40}}\b{ACTION_OBJECTS}", re.IGNORECASE)

RESTRICTED = re.compile(
    r"contract|negotiated|discount|rate\s*card|pricing\s+terms|deal\s+terms"
    r"|e-?mail\s+address|phone\s+number|contact\s+details|personal\s+(data|details|information)"
    r"|viewer\s+names|home\s+address",
    re.IGNORECASE)

VAGUE = re.compile(
    r"^\s*(is|how\s+is|how's|are)\b.*\b(doing|going|performing)\s*(well|ok|okay|good|fine|alright)?\s*\??\s*$"
    r"|^\s*is\s+.*\b(good|bad|healthy|fine|okay|ok)\s*\??\s*$",
    re.IGNORECASE)
METRIC_WORDS = re.compile(
    r"pacing|fill|revenue|impression|reach|completion|cpm|ecpm|viewer|delivery|goal|forecast",
    re.IGNORECASE)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<![\d.])(\+\d{1,3}[\s-]?)?\d{5}[\s-]?\d{5}(?![\d.])")
ACTION_CLAIM = re.compile(r"\bI\s+(have\s+|'ve\s+)?(paused|stopped|changed|updated|launched|"
                          r"deleted|increased|decreased|approved|edited)\b", re.IGNORECASE)


@dataclass
class Decision:
    action: str          # allow | refuse | escalate | clarify
    category: str        # injection | restricted_data | write_request | vague | ok
    message: str = ""


def check_input(text: str) -> Decision:
    if INJECTION.search(text):
        return Decision("refuse", "injection",
                        "I can't change how I operate or reveal my instructions. "
                        "I can help with delivery, pacing, fill, revenue, and ad-ops policy questions.")
    if RESTRICTED.search(text):
        return Decision("refuse", "restricted_data",
                        "I can't share contract terms, negotiated pricing, or personal data. "
                        "Please contact the account owner or Finance for that.")
    if WRITE_INTENT.search(text):
        return Decision("escalate", "write_request",
                        "I'm read-only and can't change campaigns. I've prepared a handoff for a human.")
    if VAGUE.search(text) and not METRIC_WORDS.search(text):
        return Decision("clarify", "vague",
                        "Which measure should I judge it on: pacing against goal, fill rate, "
                        "revenue, or ad reach? And compared with what (its goal, last week)?")
    return Decision("allow", "ok")


def check_output(text: str) -> str:
    text = EMAIL.sub("[redacted email]", text)
    text = PHONE.sub("[redacted phone]", text)
    if ACTION_CLAIM.search(text):
        text = ("Note: I'm read-only and have not changed anything. "
                "Any change needs a human. ") + ACTION_CLAIM.sub("I can't", text)
    return text
