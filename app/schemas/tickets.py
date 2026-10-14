from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["low", "medium", "high", "critical"]


class TicketOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "title": "checkout page is slow",
                "description": "",
                "priority": "high",
                "status": "open",
                "request_id": "demo-1",
                "trace_id": "1fe3198591de872ea212189dbcb19138",
                "created_at": "2026-10-14T15:49:40.196164Z",
            }
        },
    )

    id: int
    title: str
    description: str
    priority: Priority
    status: str
    request_id: str | None = Field(description="The /ask request that created it.")
    trace_id: str | None = Field(description="Open this trace in Jaeger to see how it was made.")
    created_at: datetime
