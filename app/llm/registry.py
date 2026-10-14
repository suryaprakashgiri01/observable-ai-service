"""DB-backed model inventory: lookup, availability, per-user default and fallback.

Resolution order for a request: ``X-Model-Id`` header, then body ``model_id``,
then the user's default, then the global default. An explicitly requested model
that cannot run is the caller's error (400). A *default* that cannot run (key
removed, model deactivated) degrades silently down the chain instead, so a
config change never turns into an outage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import SecretStr
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedUser
from app.core.config import Settings
from app.core.crypto import SecretBox
from app.db.models import LLMModel, UserModelConfig
from app.exceptions import AppError, ErrorCode
from app.llm.catalog import CATALOG, MOCK_MODEL_ID
from app.llm.types import ModelSpec, Provider, ResolvedModel, Tier

logger = structlog.get_logger(__name__)

SelectionSource = Literal["header", "body", "user_default", "global_default", "mock"]


@dataclass(frozen=True, slots=True)
class ModelView:
    spec: ModelSpec
    available: bool
    is_default: bool
    is_fallback: bool
    owned: bool


@dataclass(frozen=True, slots=True)
class Selection:
    model: ResolvedModel
    source: SelectionSource
    fallback: ResolvedModel | None


def _spec(row: LLMModel) -> ModelSpec:
    return ModelSpec(
        id=row.id,
        name=row.name,
        provider=Provider(row.provider),
        tier=Tier(row.tier),
        provider_model=row.provider_model,
    )


async def sync_catalog(session: AsyncSession) -> int:
    """Upsert the built-in catalog. Idempotent; returns rows inserted or updated."""
    changed = 0
    for spec in CATALOG:
        row = await session.get(LLMModel, spec.id)
        if row is None:
            session.add(
                LLMModel(
                    id=spec.id,
                    name=spec.name,
                    provider=spec.provider.value,
                    tier=spec.tier.value,
                    provider_model=spec.provider_model,
                )
            )
            changed += 1
        elif (row.name, row.provider, row.tier, row.provider_model) != (
            spec.name,
            spec.provider.value,
            spec.tier.value,
            spec.provider_model,
        ):
            row.name, row.provider = spec.name, spec.provider.value
            row.tier, row.provider_model = spec.tier.value, spec.provider_model
            changed += 1
    await session.commit()
    return changed


class ModelRegistry:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._box = SecretBox(settings.app_encryption_key.get_secret_value())

    # --- availability -------------------------------------------------------
    def _provider_key(self, provider: Provider) -> str:
        s = self._settings
        return {
            Provider.OPENAI: s.openai_api_key.get_secret_value(),
            Provider.ANTHROPIC: s.anthropic_api_key.get_secret_value(),
            Provider.GOOGLE: s.gemini_api_key.get_secret_value(),
        }.get(provider, "")

    def _to_resolved(self, row: LLMModel) -> ResolvedModel | None:
        """A callable model, or None when it cannot run right now."""
        if not row.is_active:
            return None
        spec = _spec(row)
        if spec.provider is Provider.MOCK:
            return ResolvedModel(spec=spec)
        if spec.provider is Provider.BYOM:
            if not row.base_url:
                return None
            key = self._box.decrypt(row.api_key_enc) if row.api_key_enc else None
            if row.api_key_enc and key is None:
                return None
            return ResolvedModel(
                spec=spec, base_url=row.base_url, api_key=SecretStr(key) if key else None
            )
        key = self._provider_key(spec.provider)
        return ResolvedModel(spec=spec, api_key=SecretStr(key)) if key else None

    # --- lookup ---------------------------------------------------------------
    async def _visible(self, model_id: str, user: AuthenticatedUser) -> LLMModel | None:
        row = await self._session.get(LLMModel, model_id)
        if row is None or (row.owner_user_id is not None and row.owner_user_id != user.id):
            return None
        return row

    async def _user_config(self, user: AuthenticatedUser) -> UserModelConfig | None:
        return await self._session.get(UserModelConfig, user.id)

    async def effective_ids(self, user: AuthenticatedUser) -> tuple[str, str]:
        config = await self._user_config(user)
        default = (config and config.default_model_id) or self._settings.llm_default_model_id
        fallback = (config and config.fallback_model_id) or self._settings.llm_fallback_model_id
        return default, fallback

    async def list_for_user(
        self,
        user: AuthenticatedUser,
        *,
        tier: Tier | None = None,
        provider: Provider | None = None,
    ) -> list[ModelView]:
        query = select(LLMModel).where(
            LLMModel.is_active.is_(True),
            or_(LLMModel.owner_user_id.is_(None), LLMModel.owner_user_id == user.id),
        )
        if tier is not None:
            query = query.where(LLMModel.tier == tier.value)
        if provider is not None:
            query = query.where(LLMModel.provider == provider.value)
        rows = (await self._session.scalars(query)).all()
        default_id, fallback_id = await self.effective_ids(user)
        tier_order = {Tier.ECONOMY: 0, Tier.STANDARD: 1, Tier.PREMIUM: 2}
        views = [
            ModelView(
                spec=_spec(row),
                available=self._to_resolved(row) is not None,
                is_default=row.id == default_id,
                is_fallback=row.id == fallback_id,
                owned=row.owner_user_id is not None,
            )
            for row in rows
        ]
        return sorted(views, key=lambda v: (v.spec.provider.value, tier_order[v.spec.tier]))

    # --- resolution -------------------------------------------------------------
    async def require(self, model_id: str, user: AuthenticatedUser) -> ResolvedModel:
        row = await self._visible(model_id, user)
        if row is None:
            raise AppError(ErrorCode.MODEL_NOT_FOUND, f"Unknown model '{model_id}'")
        resolved = self._to_resolved(row)
        if resolved is None:
            raise AppError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"Model '{model_id}' is not available: its provider has no credentials",
            )
        return resolved

    async def _try(self, model_id: str, user: AuthenticatedUser) -> ResolvedModel | None:
        row = await self._visible(model_id, user)
        return self._to_resolved(row) if row is not None else None

    async def resolve(
        self, user: AuthenticatedUser, *, header_id: str | None, body_id: str | None
    ) -> Selection:
        config = await self._user_config(user)
        model: ResolvedModel | None = None
        source: SelectionSource
        if header_id:
            model, source = await self.require(header_id, user), "header"
        elif body_id:
            model, source = await self.require(body_id, user), "body"
        else:
            if config and config.default_model_id:
                model, source = await self._try(config.default_model_id, user), "user_default"
            if model is None:
                model = await self._try(self._settings.llm_default_model_id, user)
                source = "global_default"
            if model is None:
                logger.warning(
                    "default model unavailable, using mock",
                    model_id=self._settings.llm_default_model_id,
                )
                model, source = await self.require(MOCK_MODEL_ID, user), "mock"

        fallback: ResolvedModel | None = None
        candidates = [config.fallback_model_id if config else None]
        candidates.append(self._settings.llm_fallback_model_id)
        for candidate in candidates:
            if candidate and candidate != model.id:
                fallback = await self._try(candidate, user)
                if fallback is not None:
                    break
        return Selection(model=model, source=source, fallback=fallback)

    # --- user configuration -------------------------------------------------------
    async def _set(self, user: AuthenticatedUser, model_id: str, *, field: str) -> None:
        await self.require(model_id, user)
        config = await self._user_config(user)
        if config is None:
            config = UserModelConfig(user_id=user.id)
            self._session.add(config)
        setattr(config, field, model_id)
        await self._session.commit()
        logger.info("user model config updated", field=field, model_id=model_id)

    async def set_default(self, user: AuthenticatedUser, model_id: str) -> None:
        await self._set(user, model_id, field="default_model_id")

    async def set_fallback(self, user: AuthenticatedUser, model_id: str) -> None:
        await self._set(user, model_id, field="fallback_model_id")

    async def register_custom(
        self,
        user: AuthenticatedUser,
        *,
        model_id: str,
        name: str,
        base_url: str,
        provider_model: str,
        tier: Tier,
        api_key: str | None,
    ) -> ModelView:
        if await self._session.get(LLMModel, model_id) is not None:
            raise AppError(ErrorCode.MODEL_CONFLICT, f"Model id '{model_id}' is already taken")
        owned = await self._session.scalars(
            select(LLMModel.id).where(LLMModel.owner_user_id == user.id)
        )
        if len(owned.all()) >= self._settings.max_custom_models_per_user:
            raise AppError(
                ErrorCode.MODEL_LIMIT_REACHED,
                f"At most {self._settings.max_custom_models_per_user} custom models per user",
            )
        row = LLMModel(
            id=model_id,
            name=name,
            provider=Provider.BYOM.value,
            tier=tier.value,
            provider_model=provider_model,
            base_url=base_url,
            api_key_enc=self._box.encrypt(api_key) if api_key else None,
            owner_user_id=user.id,
        )
        self._session.add(row)
        await self._session.commit()
        logger.info("custom model registered", model_id=model_id, base_url=base_url)
        default_id, fallback_id = await self.effective_ids(user)
        return ModelView(
            spec=_spec(row),
            available=self._to_resolved(row) is not None,
            is_default=model_id == default_id,
            is_fallback=model_id == fallback_id,
            owned=True,
        )

    async def delete_custom(self, user: AuthenticatedUser, model_id: str) -> None:
        row = await self._session.get(LLMModel, model_id)
        if row is None or row.owner_user_id != user.id:
            raise AppError(ErrorCode.MODEL_NOT_FOUND, f"No custom model '{model_id}'")
        await self._session.delete(row)
        await self._session.commit()
        logger.info("custom model deleted", model_id=model_id)
