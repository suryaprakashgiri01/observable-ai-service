from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, SecretStr

from app.llm.registry import ModelView
from app.llm.types import Provider, Tier

MODEL_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,63}$"


class ModelItem(BaseModel):
    id: str = Field(examples=["claude-haiku-5-5"])
    name: str = Field(examples=["Claude Haiku 5.5"])
    provider: Provider
    tier: Tier
    available: bool = Field(description="The provider has credentials; the model can be called.")
    is_default: bool = Field(description="Used when a request names no model.")
    is_fallback: bool = Field(description="Used when the selected model fails.")
    owned: bool = Field(description="A bring-your-own model registered by the caller.")

    @classmethod
    def from_view(cls, view: ModelView) -> ModelItem:
        return cls(
            id=view.spec.id,
            name=view.spec.name,
            provider=view.spec.provider,
            tier=view.spec.tier,
            available=view.available,
            is_default=view.is_default,
            is_fallback=view.is_fallback,
            owned=view.owned,
        )


_ITEM_EXAMPLE: dict[str, JsonValue] = {
    "id": "claude-haiku-5-5",
    "name": "Claude Haiku 5.5",
    "provider": "anthropic",
    "tier": "economy",
    "available": True,
    "is_default": False,
    "is_fallback": False,
    "owned": False,
}


class ModelListResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    _ITEM_EXAMPLE,
                    {
                        **_ITEM_EXAMPLE,
                        "id": "mock-echo",
                        "name": "Mock (rule-based)",
                        "provider": "mock",
                        "is_fallback": True,
                    },
                ],
                "total": 2,
                "default_model_id": "gpt-4.1-mini",
                "fallback_model_id": "mock-echo",
            }
        }
    )

    items: list[ModelItem]
    total: int
    default_model_id: str
    fallback_model_id: str


class SetModelRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=64, examples=["claude-haiku-5-5"])


class ModelConfigResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"default_model_id": "claude-haiku-5-5", "fallback_model_id": "mock-echo"}
        }
    )

    default_model_id: str
    fallback_model_id: str


class CustomModelRequest(BaseModel):
    """An OpenAI-compatible endpoint: Ollama, vLLM, OpenRouter, LM Studio..."""

    id: str = Field(pattern=MODEL_ID_PATTERN, examples=["my-llama"])
    name: str = Field(min_length=1, max_length=200, examples=["Llama 3.1 8B (local)"])
    base_url: HttpUrl = Field(examples=["http://localhost:11434/v1"])
    provider_model: str = Field(min_length=1, max_length=200, examples=["llama3.1:8b"])
    tier: Tier = Tier.STANDARD
    api_key: SecretStr | None = Field(
        default=None, description="Stored encrypted; never returned or logged."
    )
