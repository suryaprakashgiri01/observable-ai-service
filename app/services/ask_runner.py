"""Run one /ask end to end: agent, metrics, log line, Langfuse trace, response.

Shared by the synchronous route and the async job runner, so both produce the
same response shape and the same telemetry.
"""

from __future__ import annotations

import time
from contextlib import nullcontext

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedUser
from app.core.context import get_request_id, get_session_id
from app.core.metrics import ASK_REQUESTS
from app.core.telemetry import current_trace_id
from app.exceptions import AppError
from app.llm.langfuse_recorder import LangfuseRecorder, RequestTrace
from app.llm.orchestrator import LLMOrchestrator
from app.llm.registry import Selection
from app.schemas.ask import AskResponse, ModelUsage
from app.services.agent import AgentService

logger = structlog.get_logger(__name__)


async def run_ask(
    *,
    question: str,
    user: AuthenticatedUser,
    selection: Selection,
    orchestrator: LLMOrchestrator,
    session: AsyncSession,
    langfuse: LangfuseRecorder | None,
    fault: str | None,
    fault_count: int,
    trace_name: str = "ask",
) -> AskResponse:
    started = time.perf_counter()
    lf_context = (
        langfuse.request(name=trace_name, question=question)
        if langfuse is not None
        else nullcontext(RequestTrace(None))
    )
    with lf_context as lf:
        try:
            result = await AgentService(orchestrator, session).answer(
                question, user, selection, fault=fault, fault_count=fault_count
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
                *(["async"] if trace_name != "ask" else []),
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
