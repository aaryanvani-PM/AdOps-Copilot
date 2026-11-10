# PRD: AdOps Copilot

**Status:** Working prototype on synthetic data. **Author:** Aryan Vani.

## Problem
Ad-ops and account teams answer the same questions every day: which campaigns are behind,
why fill dropped, what the escalation SLA is. Answers live in dashboards and runbooks, so routine
questions wait for an analyst or pull ops people away from incidents. The cost is slow
responses to revenue-affecting problems such as underpacing, fill drops, and zero delivery.

## Users
- **Ad-ops specialists:** diagnose delivery problems and decide what to escalate.
- **Account managers:** need quick pacing and delivery answers for advertisers.

These personas come from my experience working with ad-ops and business teams. This prototype
has not been tested with users.

## Goals and success metrics
| Goal | Metric | Target for a pilot |
|---|---|---|
| Correct answers | Answer accuracy on the eval set | 90%+ |
| Right tool, not a guess | Tool-selection accuracy | 90%+ |
| Safe by default | Correct refusal, escalation, or clarification | 100% |
| Fast | Median latency per question | under 10 s |
| Affordable | Cost per question | measured, then compared with analyst time saved |

**Guardrail metric:** zero actions taken. The copilot has no write tools.

## Scope
**In:** read-only questions about delivery, pacing, fill, reach, revenue, and inventory forecasts;
policy questions answered from runbooks with citations; handoff to a human when an action is needed.

**Out:** changing campaigns, approving make-goods, pricing or contract questions, personal data.

## How it works
1. **Input guardrails** in plain code refuse prompt injection and restricted data, escalate action
   requests, and ask for clarification when a question is too vague.
2. **Routing** labels each question as data or policy.
3. **Tool loop:** the model calls read-only MCP tools and reads the results, with a 6-step limit.
4. **Retrieval** over runbooks (BM25), with every policy answer citing its source section.
5. **Escalation:** the model can hand off to ad-ops on-call, the account owner, or finance with a summary.
6. **Output guardrails** redact contact details and block claims of having taken an action.
7. **Tracing:** every question logs route, tools, tokens, latency, cost, and outcome.

## Rollout plan
1. **Shadow:** the copilot answers privately and ops compares its answers with their own.
2. **Assisted:** answers are shown to ops with sources, and a human confirms before acting.
3. **Self-serve for read-only questions,** once accuracy holds on live traffic.

Each stage moves forward only if eval accuracy and live spot-checks meet the targets above.

## Risks
| Risk | Mitigation |
|---|---|
| Wrong number stated confidently | Tools compute every number; evals check numbers against ground truth |
| Model talked into acting | No write tools exist; guardrails escalate action requests |
| Prompt injection | Input guardrail plus a refusal instruction; injection cases in the eval set |
| Stale or missing policy | Answers cite their source; missing data is stated, not guessed |
| Guardrails tuned to the eval set | Add new adversarial cases that were not used to write the rules |

## What I'd do next
- Run with real users in shadow mode and collect the questions they actually ask.
- Replace BM25 with embeddings once the runbook set grows.
- Add an LLM-as-judge check for answer quality alongside the exact-match checks.
