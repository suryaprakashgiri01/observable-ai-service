"""A deterministic, rule-based stand-in for an LLM.

It speaks the agent's JSON plan format, so the whole pipeline (planning, tool
dispatch, tracing) runs with no API key, and it is the last link of every
fallback chain: ``/ask`` degrades to a rule-based answer instead of a 5xx.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence

from app.llm.types import ChatMessage, LLMResult, ResolvedModel

_QUESTION_MARKER = "Question:"

_PRIORITY_WORDS = (
    ("critical", ("critical", "outage", "down", "urgent", "p0")),
    ("high", ("high", "asap", "p1", "important")),
    ("low", ("low", "minor", "p3", "whenever")),
)

_REPORT_METRICS = (
    ("average_order_value", ("average", "aov")),
    ("orders", ("orders", "order count", "volume")),
    ("revenue", ("revenue", "sales", "income")),
)


def _question(messages: Sequence[ChatMessage]) -> str:
    text = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if _QUESTION_MARKER in text:
        text = text.split(_QUESTION_MARKER, 1)[1]
    return text.strip()


def _priority(text: str) -> str:
    lowered = text.lower()
    for priority, words in _PRIORITY_WORDS:
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
            return priority
    return "medium"


def _period_days(text: str) -> int:
    lowered = text.lower()
    if match := re.search(r"(\d{1,3})\s*days?", lowered):
        return max(1, min(int(match.group(1)), 365))
    if "month" in lowered:
        return 30
    if "quarter" in lowered:
        return 90
    return 7


def _ticket_title(text: str) -> str:
    # "Create a high priority ticket: checkout is slow" -> "checkout is slow"
    title = text.split(":", 1)[1] if ":" in text else text
    return title.strip().rstrip(".")[:200] or "Untitled ticket"


def plan_for(question: str) -> dict[str, object]:
    lowered = question.lower()
    if re.search(r"\b(ticket|issue|bug|incident)\b", lowered):
        return {
            "intent": "create_ticket",
            "tool": "create_ticket",
            "args": {"title": _ticket_title(question), "priority": _priority(question)},
            "answer": "I will open a ticket for this.",
        }
    for metric, words in _REPORT_METRICS:
        if any(w in lowered for w in words) or "report" in lowered:
            chosen = metric if any(w in lowered for w in words) else "revenue"
            return {
                "intent": "generate_report",
                "tool": "generate_report",
                "args": {"metric": chosen, "period_days": _period_days(question)},
                "answer": "Here is the report.",
            }
    return {
        "intent": "general",
        "tool": None,
        "args": {},
        "answer": (
            "I can create support tickets and generate order reports "
            "(revenue, orders, average order value). "
            f'You asked: "{question[:200]}".'
        ),
    }


# Simulated latency per mock tier, so a fallback hop costs visible time in traces.
MOCK_LATENCY_SECONDS: dict[str, float] = {
    "mock-economy": 0.08,
    "mock-standard": 0.15,
    "mock-premium": 0.3,
}


class MockProvider:
    async def complete(
        self,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> LLMResult:
        delay = MOCK_LATENCY_SECONDS.get(model.spec.provider_model, 0.0)
        if delay:
            await asyncio.sleep(delay)
        question = _question(messages)
        text = json.dumps(plan_for(question))
        # Rough whitespace token counts keep the token metrics meaningful in demos.
        input_tokens = sum(len(m.content.split()) for m in messages)
        return LLMResult(text=text, input_tokens=input_tokens, output_tokens=len(text.split()))
