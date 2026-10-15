"""Who am I, and the admin-only user list (the API's real 403 path)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import Session
from app.api.openapi import AUTH_ERRORS, error_responses
from app.auth.dependencies import AdminUser, CurrentUser
from app.db.models import ApiKey, User
from app.exceptions import ErrorCode
from app.schemas.users import AdminUserList, AdminUserOut, MeOut

router = APIRouter(tags=["users"])


@router.get(
    "/me", response_model=MeOut, summary="Who am I", responses=error_responses(*AUTH_ERRORS)
)
async def me(user: CurrentUser) -> MeOut:
    """The registered user the `X-API-Key` belongs to. The console uses it to show whether
    you are authorized: 200 with your email and role, or 401."""
    return MeOut(
        id=user.id, email=user.email, name=user.name, role=user.role, key_prefix=user.key_prefix
    )


@router.get(
    "/admin/users",
    response_model=AdminUserList,
    summary="List users (admin only)",
    responses=error_responses(*AUTH_ERRORS, ErrorCode.FORBIDDEN),
)
async def admin_users(admin: AdminUser, session: Session) -> AdminUserList:
    """Every user, with their active key count and last key use. **Admin role required**: any
    other user gets **403 `FORBIDDEN`**, which counts in `auth_failures_total{reason="forbidden"}`
    and fires the `Http4xxAuthErrors` alert."""
    keys = (
        select(
            ApiKey.user_id,
            func.count(ApiKey.id).filter(ApiKey.revoked_at.is_(None)).label("active"),
            func.max(ApiKey.last_used_at).label("last_used_at"),
        )
        .group_by(ApiKey.user_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(User, keys.c.active, keys.c.last_used_at)
            .outerjoin(keys, keys.c.user_id == User.id)
            .order_by(User.created_at)
        )
    ).all()
    items = [
        AdminUserOut(
            id=u.id,
            email=u.email,
            name=u.name,
            role=u.role,
            is_active=u.is_active,
            created_at=u.created_at,
            api_keys=int(active or 0),
            last_used_at=last_used,
        )
        for u, active, last_used in rows
    ]
    return AdminUserList(items=items, total=len(items))
