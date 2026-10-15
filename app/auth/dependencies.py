"""``X-API-Key`` authentication: only registered, active users get through."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import Depends, Header, Security
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

# A plain header parameter, not a security scheme, so Swagger renders a text box on
# every protected route. Optional at the HTTP layer so a missing key renders our
# 401 envelope instead of FastAPI's 422.
# The security scheme gives Swagger its "Authorize" button (enter the key once for
# every request); the header parameter below gives each route its own text box.
# Both set the same X-API-Key header, so either works.
_api_key_scheme = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,
    scheme_name="ApiKeyAuth",
    description=(
        "API key of a registered, active user (`SEED_DEMO_API_KEY` in `.env`, or the key "
        "`make seed` printed). Entered here, it is sent on every request."
    ),
)

ApiKeyHeaderParam = Annotated[
    str | None,
    Header(
        alias=API_KEY_HEADER,
        description=(
            "**Required.** API key of a registered, active user. `make seed` creates admin, "
            "demo and analyst users; pinned keys are `SEED_*_API_KEY` in `.env`, otherwise "
            "`make seed` prints the generated key once. Shape: `oas_local_demo_3f9c...`. "
            "Missing → 401 AUTH_REQUIRED; unknown, revoked or inactive → 401 AUTH_INVALID."
        ),
    ),
]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: uuid.UUID
    email: str
    name: str
    role: str
    # Which key authenticated this request (prefix only; the key itself is never kept).
    key_prefix: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _reject(reason: str, code: ErrorCode, message: str) -> AppError:
    AUTH_FAILURES.labels(reason=reason).inc()
    trace.get_current_span().set_attribute("auth.failure_reason", reason)
    return AppError(code, message)


async def get_current_user(
    session: Session,
    api_key: ApiKeyHeaderParam = None,
    scheme_key: Annotated[str | None, Security(_api_key_scheme)] = None,
) -> AuthenticatedUser:
    api_key = api_key or scheme_key
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
    return AuthenticatedUser(
        id=user.id, email=user.email, name=user.name, role=user.role, key_prefix=key.key_prefix
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> AuthenticatedUser:
    if not user.is_admin:
        raise _reject("forbidden", ErrorCode.FORBIDDEN, "Admin role required")
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]
