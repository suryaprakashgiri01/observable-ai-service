from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["low", "medium", "high", "critical"]


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    priority: Priority
    status: str
    request_id: str | None = Field(description="The /ask request that created it.")
    trace_id: str | None = Field(description="Open this trace in Jaeger to see how it was made.")
    created_at: datetime
