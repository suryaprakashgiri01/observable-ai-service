"""Model inventory: list by tier, choose a default and a fallback, bring your own."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Query, status

from app.api.openapi import AUTH_ERRORS, error_responses
from app.api.services import Registry
from app.auth.dependencies import CurrentUser
from app.exceptions import ErrorCode
from app.llm.types import Provider, Tier
from app.schemas.models import (
    CustomModelRequest,
    ModelConfigResponse,
    ModelItem,
    ModelListResponse,
    SetModelRequest,
)

router = APIRouter(prefix="/models", tags=["models"])

TierQuery = Annotated[
    Tier | None,
    Query(
        description="Only this tier: `economy` | `standard` | `premium`.",
        openapi_examples={
            "all": {"summary": "All tiers", "value": ""},
            "economy": {"summary": "economy", "value": "economy"},
            "premium": {"summary": "premium", "value": "premium"},
        },
    ),
]
ProviderQuery = Annotated[
    Provider | None,
    Query(
        description="Only this provider: `openai` | `anthropic` | `google` | `byom` | `mock`.",
        openapi_examples={
            "all": {"summary": "All providers", "value": ""},
            "anthropic": {"summary": "anthropic", "value": "anthropic"},
            "byom": {"summary": "my bring-your-own models", "value": "byom"},
        },
    ),
]


def _set_body(purpose: str) -> object:
    return Body(
        openapi_examples={
            "mock": {"summary": f"mock-echo as my {purpose}", "value": {"model_id": "mock-echo"}},
            "haiku": {
                "summary": f"Claude Haiku 5.5 as my {purpose} (needs ANTHROPIC_API_KEY)",
                "value": {"model_id": "claude-haiku-5-5"},
            },
            "unknown": {"summary": "Unknown → 400", "value": {"model_id": "nope"}},
        }
    )


SetDefaultBody = Annotated[SetModelRequest, _set_body("default")]
SetFallbackBody = Annotated[SetModelRequest, _set_body("fallback")]
CustomBody = Annotated[
    CustomModelRequest,
    Body(
        openapi_examples={
            "ollama": {
                "summary": "Local Ollama (no key)",
                "value": {
                    "id": "my-llama",
                    "name": "Llama 3.1 8B (local Ollama)",
                    "base_url": "http://host.docker.internal:11434/v1",
                    "provider_model": "llama3.1:8b",
                    "tier": "economy",
                },
            },
            "openrouter": {
                "summary": "OpenRouter (with key, stored encrypted)",
                "value": {
                    "id": "or-mistral-small",
                    "name": "Mistral Small via OpenRouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "provider_model": "mistralai/mistral-small",
                    "tier": "standard",
                    "api_key": "sk-or-...",
                },
            },
            "invalid": {
                "summary": "Invalid → 422",
                "value": {"id": "Bad Id!", "name": "", "base_url": "not-a-url"},
            },
        }
    ),
]


@router.get(
    "",
    response_model=ModelListResponse,
    summary="List models",
    responses=error_responses(*AUTH_ERRORS),
)
async def list_models(
    user: CurrentUser,
    registry: Registry,
    tier: TierQuery = None,
    provider: ProviderQuery = None,
) -> ModelListResponse:
    """The global catalog plus the caller's own models, grouped by provider and tier.

    Pass any `id` as the `X-Model-Id` header (or `model_id` in the `/ask` body)
    to pick the model for one request.
    """
    views = await registry.list_for_user(user, tier=tier, provider=provider)
    default_id, fallback_id = await registry.effective_ids(user)
    return ModelListResponse(
        items=[ModelItem.from_view(v) for v in views],
        total=len(views),
        default_model_id=default_id,
        fallback_model_id=fallback_id,
    )


_SET_ERRORS = error_responses(*AUTH_ERRORS, ErrorCode.MODEL_NOT_FOUND, ErrorCode.MODEL_UNAVAILABLE)


@router.put(
    "/default",
    response_model=ModelConfigResponse,
    summary="Set my default model",
    responses=_SET_ERRORS,
)
async def set_default_model(
    body: SetDefaultBody, user: CurrentUser, registry: Registry
) -> ModelConfigResponse:
    """Used by `/ask` when neither `X-Model-Id` nor `model_id` is sent. Per user."""
    await registry.set_default(user, body.model_id)
    default_id, fallback_id = await registry.effective_ids(user)
    return ModelConfigResponse(default_model_id=default_id, fallback_model_id=fallback_id)


@router.put(
    "/fallback",
    response_model=ModelConfigResponse,
    summary="Set my fallback model",
    responses=_SET_ERRORS,
)
async def set_fallback_model(
    body: SetFallbackBody, user: CurrentUser, registry: Registry
) -> ModelConfigResponse:
    """Used when the selected model fails after its retries. Pick a cheap, reliable one."""
    await registry.set_fallback(user, body.model_id)
    default_id, fallback_id = await registry.effective_ids(user)
    return ModelConfigResponse(default_model_id=default_id, fallback_model_id=fallback_id)


@router.post(
    "/custom",
    response_model=ModelItem,
    status_code=status.HTTP_201_CREATED,
    summary="Register a bring-your-own model",
    responses=error_responses(
        *AUTH_ERRORS,
        ErrorCode.MODEL_CONFLICT,
        ErrorCode.MODEL_LIMIT_REACHED,
        ErrorCode.ENCRYPTION_NOT_CONFIGURED,
    ),
)
async def register_custom_model(
    body: CustomBody, user: CurrentUser, registry: Registry
) -> ModelItem:
    """Any OpenAI-compatible Chat Completions endpoint, visible only to you. The
    optional `api_key` is encrypted at rest and never returned or logged."""
    view = await registry.register_custom(
        user,
        model_id=body.id,
        name=body.name,
        base_url=str(body.base_url),
        provider_model=body.provider_model,
        tier=body.tier,
        api_key=body.api_key.get_secret_value() if body.api_key else None,
    )
    return ModelItem.from_view(view)


@router.delete(
    "/custom/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one of my custom models",
    responses=error_responses(*AUTH_ERRORS, ErrorCode.MODEL_NOT_FOUND),
)
async def delete_custom_model(model_id: str, user: CurrentUser, registry: Registry) -> None:
    await registry.delete_custom(user, model_id)
