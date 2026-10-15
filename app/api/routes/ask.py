from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Header, Path
from fastapi.responses import JSONResponse

from app.api.deps import AppSettings, Session
from app.api.openapi import AUTH_ERRORS, error_responses
from app.api.services import Jobs, Langfuse, Orchestrator, Registry
from app.auth.dependencies import CurrentUser
from app.core.context import get_request_id, get_session_id
from app.core.metrics import ASK_REQUESTS, MODEL_SELECTION
from app.core.telemetry import current_trace_id, tracer
from app.db.models import AskJob
from app.exceptions import AppError, ErrorCode
from app.schemas.ask import AskRequest, AskResponse
from app.schemas.jobs import AskJobAccepted, AskJobOut
from app.services.ask_runner import run_ask

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
FaultCountHeader = Annotated[
    int,
    Header(
        alias="X-Debug-Fault-Count",
        ge=1,
        le=5,
        description=(
            "With `X-Debug-Fault`: how many models of the fallback chain fail, from the "
            "selected one. 2 with `X-Model-Id: mock-premium` → premium and standard time out, "
            "economy answers. The last-resort `mock-echo` is never faulted."
        ),
        openapi_examples={
            "one": {"summary": "Only the selected model fails", "value": 1},
            "two": {"summary": "Two hops fail, the third answers", "value": 2},
        },
    ),
]
PreferHeader = Annotated[
    str | None,
    Header(
        alias="Prefer",
        description=(
            "`respond-async` → **202** with a `job_id` right away; the answer is produced in "
            "the background. Poll `GET /ask/jobs/{job_id}`; with `KAFKA_ENABLED` every status "
            "change is also published to the ask-status topic. Omit it for a synchronous 200."
        ),
        openapi_examples={
            "sync": {"summary": "Synchronous (default)", "value": ""},
            "async": {"summary": "Async job (202)", "value": "respond-async"},
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


_ACCEPTED: dict[int | str, dict[str, Any]] = {
    202: {
        "model": AskJobAccepted,
        "description": "`Prefer: respond-async`: job accepted; poll `status_url`.",
    }
}


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a question",
    responses={
        **_ACCEPTED,
        **error_responses(
            *AUTH_ERRORS,
            ErrorCode.MODEL_NOT_FOUND,
            ErrorCode.MODEL_UNAVAILABLE,
            ErrorCode.TOOL_FAILED,
            ErrorCode.LLM_UNAVAILABLE,
        ),
    },
)
async def ask(
    body: AskBody,
    user: CurrentUser,
    registry: Registry,
    orchestrator: Orchestrator,
    session: Session,
    settings: AppSettings,
    langfuse: Langfuse,
    jobs: Jobs,
    x_model_id: ModelIdHeader = None,
    x_debug_fault: FaultHeader = None,
    x_debug_fault_count: FaultCountHeader = 1,
    prefer: PreferHeader = None,
) -> AskResponse | JSONResponse:
    """Answer a question with an LLM-planned agent that can open tickets and build
    order reports. Every response carries the `request_id`, `session_id` and
    `trace_id` that join its log lines, spans and Langfuse trace.

    Send `Prefer: respond-async` to get a **202** and a job id instead."""
    fault = x_debug_fault if settings.enable_fault_injection else None
    if fault:
        logger.warning("fault injection active", fault=fault, fault_count=x_debug_fault_count)

    # Resolved before anything is queued, so a bad model is a 400 in both modes.
    try:
        with tracer.start_as_current_span("model.resolve") as span:
            selection = await registry.resolve(user, header_id=x_model_id, body_id=body.model_id)
            span.set_attributes(
                {
                    "llm.selection.source": selection.source,
                    "llm.model_id": selection.model.id,
                    "llm.tier": selection.model.spec.tier.value,
                    "llm.fallback_chain": ",".join(m.id for m in selection.fallbacks),
                }
            )
    except AppError as exc:
        ASK_REQUESTS.labels(intent="unknown", outcome=exc.code.value.lower()).inc()
        raise
    MODEL_SELECTION.labels(source=selection.source).inc()

    if prefer and "respond-async" in prefer.lower():
        job = await jobs.submit(
            user=user,
            question=body.question,
            selection=selection,
            fault=fault,
            fault_count=x_debug_fault_count,
        )
        accepted = AskJobAccepted(
            job_id=job.id,
            status="queued",
            status_url=f"/ask/jobs/{job.id}",
            request_id=get_request_id(),
            session_id=get_session_id(),
            trace_id=current_trace_id(),
        )
        return JSONResponse(
            status_code=202,
            content=accepted.model_dump(mode="json"),
            headers={"Location": accepted.status_url, "Preference-Applied": "respond-async"},
        )

    return await run_ask(
        question=body.question,
        user=user,
        selection=selection,
        orchestrator=orchestrator,
        session=session,
        langfuse=langfuse,
        fault=fault,
        fault_count=x_debug_fault_count,
    )


@router.get(
    "/ask/jobs/{job_id}",
    response_model=AskJobOut,
    summary="Get an async /ask job",
    responses=error_responses(*AUTH_ERRORS, ErrorCode.JOB_NOT_FOUND),
)
async def get_job(
    job_id: Annotated[uuid.UUID, Path(description="`job_id` from the 202 response.")],
    user: CurrentUser,
    session: Session,
) -> AskJobOut:
    """Status, attempts, the model currently answering, every transition so far and,
    once `completed`, the same body a synchronous `/ask` returns. Only the owner sees
    a job; anyone else gets 404."""
    job = await session.get(AskJob, job_id)
    if job is None or job.user_id != user.id:
        raise AppError(ErrorCode.JOB_NOT_FOUND, f"Job {job_id} not found")
    return AskJobOut.model_validate(job)
