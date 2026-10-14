from __future__ import annotations

import time
from typing import Annotated

import structlog
from fastapi import APIRouter, Header

from app.api.deps import AppSettings, Session
from app.api.services import Orchestrator, Registry
from app.auth.dependencies import CurrentUser
from app.core.context import get_request_id
from app.core.metrics import ASK_REQUESTS, MODEL_SELECTION
from app.core.telemetry import current_trace_id, tracer
from app.exceptions import AppError
from app.schemas.ask import AskRequest, AskResponse, ModelUsage
from app.services.agent import AgentService

router = APIRouter(tags=["ask"])
logger = structlog.get_logger(__name__)

ModelIdHeader = Annotated[
    str | None,
    Header(
        alias="X-Model-Id",
        max_length=64,
        description="Model for this request; overrides the body `model_id`. See GET /models.",
    ),
]
FaultHeader = Annotated[
    str | None,
    Header(
        alias="X-Debug-Fault",
        description="Demo only (ENABLE_FAULT_INJECTION): llm_timeout | llm_error | tool_error.",
    ),
]


@router.post("/ask", response_model=AskResponse, summary="Ask a question")
async def ask(
    body: AskRequest,
    user: CurrentUser,
    registry: Registry,
    orchestrator: Orchestrator,
    session: Session,
    settings: AppSettings,
    x_model_id: ModelIdHeader = None,
    x_debug_fault: FaultHeader = None,
) -> AskResponse:
    """Answer a question with an LLM-planned agent that can open tickets and build
    order reports. Every response carries the `request_id` and `trace_id` that
    join its log lines, metrics exemplars and spans."""
    started = time.perf_counter()
    fault = x_debug_fault if settings.enable_fault_injection else None
    if fault:
        logger.warning("fault injection active", fault=fault)

    try:
        with tracer.start_as_current_span("model.resolve") as span:
            selection = await registry.resolve(user, header_id=x_model_id, body_id=body.model_id)
            span.set_attributes(
                {
                    "llm.selection.source": selection.source,
                    "llm.model_id": selection.model.id,
                    "llm.tier": selection.model.spec.tier.value,
                    "llm.fallback_model_id": selection.fallback.id if selection.fallback else "",
                }
            )
        MODEL_SELECTION.labels(source=selection.source).inc()

        result = await AgentService(orchestrator, session).answer(
            body.question, user, selection, fault=fault
        )
    except AppError as exc:
        ASK_REQUESTS.labels(intent="unknown", outcome=exc.code.value.lower()).inc()
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
    return AskResponse(
        request_id=get_request_id(),
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
