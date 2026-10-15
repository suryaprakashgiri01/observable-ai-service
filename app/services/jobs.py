"""Async /ask jobs: run in a tracked background task, report every transition.

Status lives in Postgres (``ask_jobs``) and, when ``KAFKA_ENABLED``, is also
published to the ask-status topic. One in-process runner is enough for a single
replica; with several replicas the jobs would be consumed from Kafka instead.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace
from sqlalchemy import update

from app.auth.dependencies import AuthenticatedUser
from app.core.context import get_request_id, get_session_id
from app.core.metrics import ASK_JOBS, ASK_JOBS_IN_FLIGHT
from app.core.telemetry import current_trace_id, tracer
from app.db.models import AskJob
from app.db.session import Database
from app.exceptions import AppError, ErrorCode
from app.integrations.events import EventPublisher
from app.llm.langfuse_recorder import LangfuseRecorder
from app.llm.orchestrator import LLMOrchestrator
from app.llm.registry import Selection
from app.llm.status import set_status_hook
from app.services.ask_runner import run_ask

logger = structlog.get_logger(__name__)

TERMINAL = ("completed", "failed")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobRunner:
    def __init__(
        self,
        db: Database,
        orchestrator: LLMOrchestrator,
        events: EventPublisher,
        langfuse: LangfuseRecorder | None,
    ) -> None:
        self._db = db
        self._orchestrator = orchestrator
        self._events = events
        self._langfuse = langfuse
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(
        self,
        *,
        user: AuthenticatedUser,
        question: str,
        selection: Selection,
        fault: str | None,
        fault_count: int,
    ) -> AskJob:
        job = AskJob(
            user_id=user.id,
            question=question,
            model_requested=selection.model.id,
            status="queued",
            events=[{"status": "queued", "at": _now(), "model_id": selection.model.id}],
            request_id=get_request_id(),
            session_id=get_session_id(),
            trace_id=current_trace_id(),
        )
        async with self._db.sessionmaker() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
        ASK_JOBS.labels(status="queued").inc()
        await self._publish(job, {"status": "queued", "model_id": selection.model.id})
        logger.info("ask job queued", job_id=str(job.id), model_id=selection.model.id)

        # The task copies the current context: request/session/user ids, structlog
        # vars and the OTel span, so the job stays in the request's trace and logs.
        task = asyncio.create_task(
            self._run(job.id, user, question, selection, fault, fault_count),
            name=f"ask-job-{job.id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(
        self,
        job_id: uuid.UUID,
        user: AuthenticatedUser,
        question: str,
        selection: Selection,
        fault: str | None,
        fault_count: int,
    ) -> None:
        ASK_JOBS_IN_FLIGHT.inc()
        structlog.contextvars.bind_contextvars(job_id=str(job_id))
        with tracer.start_as_current_span("ask.job", attributes={"job.id": str(job_id)}) as span:

            async def hook(status: str, fields: dict[str, Any]) -> None:
                await self._transition(job_id, status, fields)

            set_status_hook(hook)
            try:
                async with self._db.sessionmaker() as work:
                    response = await run_ask(
                        question=question,
                        user=user,
                        selection=selection,
                        orchestrator=self._orchestrator,
                        session=work,
                        langfuse=self._langfuse,
                        fault=fault,
                        fault_count=fault_count,
                        trace_name="ask.job",
                    )
                await self._finish(job_id, "completed", result=response.model_dump(mode="json"))
            except AppError as exc:
                span.set_status(trace.Status(trace.StatusCode.ERROR, exc.code.value))
                await self._finish(
                    job_id, "failed", error={"code": exc.code.value, "message": exc.message}
                )
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
                logger.error("ask job crashed", exc_info=exc)
                await self._finish(
                    job_id,
                    "failed",
                    error={"code": ErrorCode.INTERNAL_ERROR.value, "message": "Job failed"},
                )
            finally:
                set_status_hook(None)
                ASK_JOBS_IN_FLIGHT.dec()

    async def _transition(
        self, job_id: uuid.UUID, status: str, fields: dict[str, Any]
    ) -> AskJob | None:
        event = {"status": status, "at": _now(), **fields}
        async with self._db.sessionmaker() as session:
            job = await session.get(AskJob, job_id)
            if job is None:
                return None
            job.status = status
            if fields.get("model_id"):
                job.current_model = str(fields["model_id"])
            if status == "running":
                job.attempts += 1
            # Reassign: in-place mutation of a JSON column is not tracked.
            job.events = [*job.events, event]
            if status in TERMINAL:
                job.completed_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(job)
        ASK_JOBS.labels(status=status).inc()
        log = logger.warning if status in ("retrying", "fallback", "failed") else logger.info
        log("ask job status", job_id=str(job_id), status=status, **fields)
        await self._publish(job, fields | {"status": status})
        return job

    async def _finish(
        self,
        job_id: uuid.UUID,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        async with self._db.sessionmaker() as session:
            job = await session.get(AskJob, job_id)
            if job is not None:
                job.result, job.error = result, error
                await session.commit()
        fields: dict[str, Any] = {}
        if result is not None:
            fields["model_id"] = result["model"]["used"]
        if error is not None:
            fields["error"] = error
        await self._transition(job_id, status, fields)

    async def _publish(self, job: AskJob, fields: dict[str, Any]) -> None:
        await self._events.publish(
            "ask_status",
            str(job.id),
            {
                "job_id": str(job.id),
                "attempts": job.attempts,
                "current_model": job.current_model,
                "request_id": job.request_id,
                "session_id": job.session_id,
                "trace_id": job.trace_id,
                "at": _now(),
                **fields,
            },
        )

    async def recover_interrupted(self) -> int:
        """Jobs left unfinished by a previous process can never complete: fail them."""
        async with self._db.sessionmaker() as session:
            result = await session.execute(
                update(AskJob)
                .where(AskJob.status.not_in(TERMINAL))
                .values(
                    status="failed",
                    error={"code": "INTERRUPTED", "message": "Service restarted mid-job"},
                    completed_at=datetime.now(UTC),
                )
            )
            await session.commit()
        count = int(getattr(result, "rowcount", 0) or 0)
        if count:
            logger.warning("interrupted ask jobs marked failed", count=count)
        return count

    async def shutdown(self, grace_seconds: float = 10.0) -> None:
        if not self._tasks:
            return
        logger.info("waiting for ask jobs", running=len(self._tasks))
        _, pending = await asyncio.wait(set(self._tasks), timeout=grace_seconds)
        for task in pending:
            task.cancel()
