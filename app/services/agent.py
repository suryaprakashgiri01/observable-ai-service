"""The /ask agent: one planning call, then a deterministic tool step.

The model is asked for a JSON plan (intent, tool, args, answer) rather than
vendor-native tool calling, so every provider in the catalog - including BYOM
endpoints and the mock - can drive the same tools. The final answer for a tool
is templated from the tool's result, not generated, which saves a second LLM
round trip and keeps business numbers exactly what the tool computed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from opentelemetry import trace
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedUser
from app.core.metrics import TOOL_CALLS
from app.core.telemetry import tracer
from app.exceptions import AppError, ErrorCode
from app.llm.orchestrator import Completion, LLMOrchestrator
from app.llm.registry import Selection
from app.llm.types import ChatMessage
from app.schemas.tickets import TicketOut
from app.services.tools.reports import ReportOut, generate_report
from app.services.tools.tickets import create_ticket

logger = structlog.get_logger(__name__)

FAULT_TOOL_ERROR = "tool_error"

Intent = Literal["create_ticket", "generate_report", "general"]

SYSTEM_PROMPT = """You are the planning step of an operations assistant.
Read the user's question and reply with ONE JSON object, no prose, no code fences:

{"intent": "create_ticket" | "generate_report" | "general",
 "tool": "create_ticket" | "generate_report" | null,
 "args": {...},
 "answer": "one short sentence for the user"}

Tools:
- create_ticket: args {"title": str (<= 120 chars), "priority": "low"|"medium"|"high"|"critical",
  "description": str}. Use when the user reports a problem or asks to open a ticket/issue.
- generate_report: args {"metric": "revenue"|"orders"|"average_order_value",
  "period_days": int 1-90}. Use for questions about sales, revenue, orders or AOV.
- Otherwise intent "general", tool null, args {}, and answer the question briefly.
"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class Plan(BaseModel):
    intent: Intent = "general"
    tool: Literal["create_ticket", "generate_report"] | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""


@dataclass(frozen=True, slots=True)
class AgentResult:
    answer: str
    intent: Intent
    action: dict[str, Any] | None
    completion: Completion
    plan_valid: bool


def parse_plan(text: str) -> Plan | None:
    match = _JSON_OBJECT.search(text.strip())
    if match is None:
        return None
    try:
        return Plan.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError):
        return None


class AgentService:
    def __init__(self, orchestrator: LLMOrchestrator, session: AsyncSession) -> None:
        self._orchestrator = orchestrator
        self._session = session

    async def answer(
        self,
        question: str,
        user: AuthenticatedUser,
        selection: Selection,
        *,
        fault: str | None = None,
    ) -> AgentResult:
        messages = [
            ChatMessage("system", SYSTEM_PROMPT),
            ChatMessage("user", f"Question: {question}"),
        ]
        with tracer.start_as_current_span("agent.plan") as span:
            completion = await self._orchestrator.complete(selection, messages, fault=fault)
            plan = parse_plan(completion.result.text)
            plan_valid = plan is not None
            span.set_attribute("agent.plan_valid", plan_valid)
            if plan is None:
                # A model that ignores the format still produced an answer; show it
                # rather than failing, and make the miss visible in logs and the span.
                logger.warning(
                    "llm plan not valid JSON; degrading to plain answer",
                    model_id=completion.model.id,
                    output_preview=completion.result.text[:200],
                )
                plan = Plan(intent="general", answer=completion.result.text.strip()[:2000])
            span.set_attribute("agent.intent", plan.intent)
            if plan.tool:
                span.set_attribute("agent.tool", plan.tool)

        if plan.tool is None:
            return AgentResult(plan.answer, plan.intent, None, completion, plan_valid)

        action, answer = await self._run_tool(plan, user, fault=fault)
        return AgentResult(answer, plan.intent, action, completion, plan_valid)

    async def _run_tool(
        self, plan: Plan, user: AuthenticatedUser, *, fault: str | None
    ) -> tuple[dict[str, Any], str]:
        tool = plan.tool or "none"
        with tracer.start_as_current_span(f"tool.{tool}", attributes={"tool.name": tool}) as span:
            try:
                if fault == FAULT_TOOL_ERROR:
                    raise RuntimeError("injected tool failure")
                if plan.tool == "create_ticket":
                    ticket = await create_ticket(
                        self._session,
                        user,
                        title=str(plan.args.get("title", "")),
                        priority=str(plan.args.get("priority", "medium")),
                        description=str(plan.args.get("description", "")),
                    )
                    span.set_attribute("ticket.id", ticket.id)
                    result: tuple[dict[str, Any], str] = (
                        {"type": "ticket_created", "ticket": ticket.model_dump(mode="json")},
                        _ticket_answer(ticket),
                    )
                else:
                    report = generate_report(
                        metric=str(plan.args.get("metric", "revenue")),
                        period_days=_as_int(plan.args.get("period_days"), default=7),
                    )
                    result = (
                        {"type": "report", "report": report.model_dump(mode="json")},
                        _report_answer(report),
                    )
            except AppError:
                TOOL_CALLS.labels(tool=tool, outcome="error").inc()
                raise
            except Exception as exc:
                TOOL_CALLS.labels(tool=tool, outcome="error").inc()
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
                logger.error("tool failed", tool=tool, error=str(exc), exc_info=exc)
                raise AppError(ErrorCode.TOOL_FAILED, f"Tool '{tool}' failed") from exc
            TOOL_CALLS.labels(tool=tool, outcome="success").inc()
            return result


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ticket_answer(ticket: TicketOut) -> str:
    return f"Created ticket #{ticket.id} ({ticket.priority} priority): {ticket.title}"


def _report_answer(report: ReportOut) -> str:
    label = report.metric.replace("_", " ")
    value = f"{report.value:,.0f}" if report.metric == "orders" else f"${report.value:,.2f}"
    top = report.top_products[0]["product"] if report.top_products else "n/a"
    return (
        f"{label.capitalize()} for the last {report.period_days} days: {value} "
        f"across {report.order_count} orders. Top product: {top}."
    )
