from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

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
    request_id: str | None
    trace_id: str | None = Field(description="Search this id in Jaeger (and Langfuse).")
    answer: str
    intent: str
    action: dict[str, Any] | None
    model: ModelUsage
    latency_ms: float
