"""``X-API-Key`` authentication: only registered, active users get through."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader
from opentelemetry import trace
from sqlalchemy import select, update

from app.api.deps import Session
from app.auth.keys import display_prefix, hash_api_key
from app.core.context import set_user_id
from app.core.metrics import AUTH_FAILURES
from app.db.models import ApiKey, User
from app.exceptions import AppError, ErrorCode

API_KEY_HEADER = "X-API-Key"

# auto_error=False: a missing header must render our envelope, not FastAPI's 403.
_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: uuid.UUID
    email: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _reject(reason: str, code: ErrorCode, message: str) -> AppError:
    AUTH_FAILURES.labels(reason=reason).inc()
    trace.get_current_span().set_attribute("auth.failure_reason", reason)
    return AppError(code, message)


async def get_current_user(
    session: Session,
    api_key: Annotated[str | None, Security(_api_key_scheme)] = None,
) -> AuthenticatedUser:
    if not api_key:
        raise _reject("missing", ErrorCode.AUTH_REQUIRED, f"{API_KEY_HEADER} header is required")

    row = (
        await session.execute(
            select(ApiKey, User)
            .join(User, User.id == ApiKey.user_id)
            .where(ApiKey.key_hash == hash_api_key(api_key))
        )
    ).first()
    if row is None:
        logger.warning("api key rejected", reason="unknown", key_prefix=display_prefix(api_key))
        raise _reject("unknown", ErrorCode.AUTH_INVALID, "Invalid API key")

    key, user = row
    if key.revoked_at is not None:
        raise _reject("revoked", ErrorCode.AUTH_INVALID, "Invalid API key")
    if not user.is_active:
        raise _reject("inactive_user", ErrorCode.AUTH_INVALID, "Invalid API key")

    await session.execute(
        update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=datetime.now(UTC))
    )
    await session.commit()

    user_id = str(user.id)
    set_user_id(user_id)
    structlog.contextvars.bind_contextvars(user_id=user_id)
    trace.get_current_span().set_attribute("enduser.id", user_id)
    return AuthenticatedUser(id=user.id, email=user.email, name=user.name, role=user.role)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> AuthenticatedUser:
    if not user.is_admin:
        raise _reject("forbidden", ErrorCode.FORBIDDEN, "Admin role required")
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]
