"""Model inventory: list by tier, choose a default and a fallback, bring your own."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.services import Registry
from app.auth.dependencies import CurrentUser
from app.llm.types import Provider, Tier
from app.schemas.models import (
    CustomModelRequest,
    ModelConfigResponse,
    ModelItem,
    ModelListResponse,
    SetModelRequest,
)

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelListResponse, summary="List models")
async def list_models(
    user: CurrentUser,
    registry: Registry,
    tier: Tier | None = None,
    provider: Provider | None = None,
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


@router.put("/default", response_model=ModelConfigResponse, summary="Set my default model")
async def set_default_model(
    body: SetModelRequest, user: CurrentUser, registry: Registry
) -> ModelConfigResponse:
    await registry.set_default(user, body.model_id)
    default_id, fallback_id = await registry.effective_ids(user)
    return ModelConfigResponse(default_model_id=default_id, fallback_model_id=fallback_id)


@router.put("/fallback", response_model=ModelConfigResponse, summary="Set my fallback model")
async def set_fallback_model(
    body: SetModelRequest, user: CurrentUser, registry: Registry
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
)
async def register_custom_model(
    body: CustomModelRequest, user: CurrentUser, registry: Registry
) -> ModelItem:
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
)
async def delete_custom_model(model_id: str, user: CurrentUser, registry: Registry) -> None:
    await registry.delete_custom(user, model_id)
