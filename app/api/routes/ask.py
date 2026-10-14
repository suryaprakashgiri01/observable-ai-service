from __future__ import annotations

import time
from contextlib import nullcontext
from typing import Annotated

import structlog
from fastapi import APIRouter, Body, Header

from app.api.deps import AppSettings, Session
from app.api.openapi import AUTH_ERRORS, error_responses
from app.api.services import Langfuse, Orchestrator, Registry
from app.auth.dependencies import CurrentUser
from app.core.context import get_request_id, get_session_id
from app.core.metrics import ASK_REQUESTS, MODEL_SELECTION
from app.core.telemetry import current_trace_id, tracer
from app.exceptions import AppError, ErrorCode
from app.llm.langfuse_recorder import RequestTrace
from app.schemas.ask import AskRequest, AskResponse, ModelUsage
from app.services.agent import AgentService

router = APIRouter(tags=["ask"])
logger = structlog.get_logger(__name__)

ModelIdHeader = Annotated[
    str | None,
    Header(
        alias="X-Model-Id",
        max_length=64,
        description=(
            "Model for this request; wins over the body `model_id`. Any `id` from "
            "`GET /models`. Unknown → 400 MODEL_NOT_FOUND; no provider key → 400 "
            "MODEL_UNAVAILABLE. Omit it to use your default."
        ),
        openapi_examples={
            "default": {"summary": "Use my default model", "value": ""},
            "mock": {"summary": "mock-echo (always available)", "value": "mock-echo"},
            "anthropic": {"summary": "Claude Haiku 5.5 (economy)", "value": "claude-haiku-5-5"},
            "openai": {"summary": "GPT-4.1 mini (standard)", "value": "gpt-4.1-mini"},
            "google": {"summary": "Gemini 2.5 Pro (premium)", "value": "gemini-2.5-pro"},
            "unknown": {"summary": "Unknown → 400", "value": "nope"},
        },
    ),
]
FaultHeader = Annotated[
    str | None,
    Header(
        alias="X-Debug-Fault",
        description=(
            "Demo only; ignored unless `ENABLE_FAULT_INJECTION=true`. `llm_timeout`: the "
            "selected model times out on every attempt, so retries then fallback. `llm_error`: "
            "permanent error, fallback without retry. `tool_error`: the business action "
            "fails → 502."
        ),
        openapi_examples={
            "none": {"summary": "No fault", "value": ""},
            "llm_timeout": {"summary": "Timeouts → retries → fallback", "value": "llm_timeout"},
            "llm_error": {"summary": "Permanent error → fallback", "value": "llm_error"},
            "tool_error": {"summary": "Tool fails → 502", "value": "tool_error"},
        },
    ),
]
AskBody = Annotated[
    AskRequest,
    Body(
        openapi_examples={
            "ticket": {
                "summary": "Create a ticket",
                "value": {"question": "Create a high priority ticket: checkout page is slow"},
            },
            "revenue": {
                "summary": "Revenue report",
                "value": {"question": "Give me a revenue report for the last 7 days"},
            },
            "orders": {
                "summary": "Orders report",
                "value": {"question": "How many orders in the last 30 days?"},
            },
            "aov": {
                "summary": "Average order value",
                "value": {"question": "What is the average order value this quarter?"},
            },
            "general": {"summary": "General question", "value": {"question": "What can you do?"}},
            "body_model": {
                "summary": "Pick the model in the body",
                "value": {"question": "Orders in the last 14 days", "model_id": "mock-echo"},
            },
            "invalid": {"summary": "Empty question → 422", "value": {"question": ""}},
        }
    ),
]


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a question",
    responses=error_responses(
        *AUTH_ERRORS,
        ErrorCode.MODEL_NOT_FOUND,
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.TOOL_FAILED,
        ErrorCode.LLM_UNAVAILABLE,
    ),
)
async def ask(
    body: AskBody,
    user: CurrentUser,
    registry: Registry,
    orchestrator: Orchestrator,
    session: Session,
    settings: AppSettings,
    langfuse: Langfuse,
    x_model_id: ModelIdHeader = None,
    x_debug_fault: FaultHeader = None,
) -> AskResponse:
    """Answer a question with an LLM-planned agent that can open tickets and build
    order reports. Every response carries the `request_id`, `session_id` and
    `trace_id` that join its log lines, spans and Langfuse trace."""
    started = time.perf_counter()
    fault = x_debug_fault if settings.enable_fault_injection else None
    if fault:
        logger.warning("fault injection active", fault=fault)

    lf_context = (
        langfuse.request(name="ask", question=body.question)
        if langfuse is not None
        else nullcontext(RequestTrace(None))
    )
    with lf_context as lf:
        try:
            with tracer.start_as_current_span("model.resolve") as span:
                selection = await registry.resolve(
                    user, header_id=x_model_id, body_id=body.model_id
                )
                span.set_attributes(
                    {
                        "llm.selection.source": selection.source,
                        "llm.model_id": selection.model.id,
                        "llm.tier": selection.model.spec.tier.value,
                        "llm.fallback_model_id": (
                            selection.fallback.id if selection.fallback else ""
                        ),
                    }
                )
            MODEL_SELECTION.labels(source=selection.source).inc()

            result = await AgentService(orchestrator, session).answer(
                body.question, user, selection, fault=fault
            )
        except AppError as exc:
            ASK_REQUESTS.labels(intent="unknown", outcome=exc.code.value.lower()).inc()
            lf.finish(
                output={"error": exc.code.value, "message": exc.message},
                tags=["error", exc.code.value],
                error=exc.message,
            )
            raise

        completion = result.completion
        outcome = "fallback" if completion.fallback_used else "success"
        ASK_REQUESTS.labels(intent=result.intent, outcome=outcome).inc()
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "ask answered",
            intent=result.intent,
            model_requested=selection.model.id,
            model_used=completion.model.id,
            selection_source=selection.source,
            fallback_used=completion.fallback_used,
            attempts=completion.attempts,
            plan_valid=result.plan_valid,
            latency_ms=latency_ms,
        )
        lf.finish(
            output={"answer": result.answer, "intent": result.intent, "action": result.action},
            tags=[
                result.intent,
                completion.model.id,
                *(["fallback"] if completion.fallback_used else []),
                *([f"fault:{fault}"] if fault else []),
            ],
        )
        return AskResponse(
            request_id=get_request_id(),
            session_id=get_session_id(),
            trace_id=current_trace_id(),
            answer=result.answer,
            intent=result.intent,
            action=result.action,
            model=ModelUsage(
                requested=selection.model.id,
                used=completion.model.id,
                provider=completion.model.spec.provider,
                tier=completion.model.spec.tier,
                source=selection.source,
                fallback_used=completion.fallback_used,
                attempts=completion.attempts,
                chain=completion.chain,
            ),
            latency_ms=latency_ms,
        )
