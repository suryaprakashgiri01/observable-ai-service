from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.llm.types import Provider, Tier

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class AskRequest(BaseModel):
    question: Question = Field(examples=["Create a high priority ticket: checkout page is slow"])
    model_id: str | None = Field(
        default=None,
        max_length=64,
        description="Model for this request. The X-Model-Id header wins over this field.",
        examples=["claude-haiku-5-5"],
    )


class ModelUsage(BaseModel):
    requested: str = Field(description="The model selected for the request.")
    used: str = Field(description="The model that actually answered.")
    provider: Provider
    tier: Tier
    source: str = Field(description="header | body | user_default | global_default | mock")
    fallback_used: bool
    attempts: int = Field(description="LLM call attempts across the whole fallback chain.")
    chain: list[str] = Field(description="Models tried, in order.")


class AskResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "demo-1",
                "session_id": "demo-session-1",
                "trace_id": "1fe3198591de872ea212189dbcb19138",
                "answer": "Created ticket #1 (high priority): checkout page is slow",
                "intent": "create_ticket",
                "action": {
                    "type": "ticket_created",
                    "ticket": {
                        "id": 1,
                        "title": "checkout page is slow",
                        "description": "",
                        "priority": "high",
                        "status": "open",
                        "request_id": "demo-1",
                        "trace_id": "1fe3198591de872ea212189dbcb19138",
                        "created_at": "2026-10-14T15:49:40.196164Z",
                    },
                },
                "model": {
                    "requested": "claude-haiku-5-5",
                    "used": "claude-haiku-5-5",
                    "provider": "anthropic",
                    "tier": "economy",
                    "source": "header",
                    "fallback_used": False,
                    "attempts": 1,
                    "chain": ["claude-haiku-5-5"],
                },
                "latency_ms": 812.4,
            }
        }
    )

    request_id: str | None
    session_id: str | None = Field(
        description="`X-Session-ID` if sent, else the request id. A Langfuse session."
    )
    trace_id: str | None = Field(description="Search this id in Jaeger (and Langfuse).")
    answer: str
    intent: str = Field(description="create_ticket | generate_report | general")
    action: dict[str, Any] | None = Field(
        description=(
            '`{"type": "ticket_created", "ticket": Ticket}` or `{"type": "report", "report": '
            "{metric, period_days, value, order_count, by_region, top_products, "
            "excluded_refunded}}`; null for general questions."
        )
    )
    model: ModelUsage
    latency_ms: float
