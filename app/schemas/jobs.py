from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["queued", "running", "retrying", "fallback", "completed", "failed"]


class AskJobAccepted(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "9b1f0c2d-4a7e-4c1b-8f3e-2a6d5c7b8e90",
                "status": "queued",
                "status_url": "/ask/jobs/9b1f0c2d-4a7e-4c1b-8f3e-2a6d5c7b8e90",
                "request_id": "async-1",
                "session_id": "async-demo",
                "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
            }
        }
    )

    job_id: uuid.UUID
    status: JobStatus
    status_url: str = Field(description="Poll this with the same X-API-Key.")
    request_id: str | None
    session_id: str | None
    trace_id: str | None = Field(description="The job runs inside this trace (span `ask.job`).")


class AskJobOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "9b1f0c2d-4a7e-4c1b-8f3e-2a6d5c7b8e90",
                "status": "completed",
                "question": "Give me a revenue report for the last 7 days",
                "model_requested": "mock-premium",
                "current_model": "mock-economy",
                "attempts": 7,
                "events": [
                    {"status": "queued", "at": "2026-10-15T10:00:00Z", "model_id": "mock-premium"},
                    {"status": "running", "model_id": "mock-premium", "attempt": 1},
                    {"status": "retrying", "model_id": "mock-premium", "reason": "timeout"},
                    {
                        "status": "fallback",
                        "from_model": "mock-premium",
                        "model_id": "mock-standard",
                    },
                    {"status": "completed", "model_id": "mock-economy"},
                ],
                "result": {
                    "answer": "Revenue for the last 7 days: ...",
                    "intent": "generate_report",
                },
                "error": None,
                "request_id": "async-1",
                "session_id": "async-demo",
                "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
                "created_at": "2026-10-15T10:00:00Z",
                "updated_at": "2026-10-15T10:00:07Z",
                "completed_at": "2026-10-15T10:00:07Z",
            }
        },
    )

    id: uuid.UUID
    status: JobStatus
    question: str
    model_requested: str
    current_model: str | None
    attempts: int
    events: list[dict[str, Any]] = Field(description="Every status transition, oldest first.")
    result: dict[str, Any] | None = Field(description="The /ask response, once completed.")
    error: dict[str, Any] | None = Field(description="`{code, message}` if failed.")
    request_id: str | None
    session_id: str | None
    trace_id: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
