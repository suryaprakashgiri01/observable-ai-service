"""Create the initial users and their API keys. Idempotent: ``python -m scripts.seed_users``.

A key comes from ``SEED_<NAME>_API_KEY`` when set, so demos and scripts can
use a known value; otherwise one is generated and printed once. Only its hash
is stored, so a generated key that was not copied must be re-issued.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keys import display_prefix, generate_api_key, hash_api_key
from app.core.config import get_settings
from app.db.models import ApiKey, User
from app.db.session import Database


@dataclass(frozen=True)
class SeedUser:
    env_name: str
    email: str
    name: str
    role: str


SEED_USERS = (
    SeedUser("ADMIN", "admin@example.com", "Admin", "admin"),
    SeedUser("DEMO", "demo@example.com", "Demo User", "user"),
    SeedUser("ANALYST", "analyst@example.com", "Analyst", "user"),
)


def _pinned_key(seed: SeedUser) -> str | None:
    value = os.environ.get(f"SEED_{seed.env_name}_API_KEY", "").strip()
    if value:
        return value
    # Fall back to .env so `make seed` works without exporting variables.
    try:
        with open(".env", encoding="utf-8") as handle:
            for line in handle:
                key, _, raw = line.partition("=")
                if key.strip() == f"SEED_{seed.env_name}_API_KEY" and raw.strip():
                    return raw.strip()
    except FileNotFoundError:
        pass
    return None


async def _seed_one(session: AsyncSession, seed: SeedUser) -> str:
    user = await session.scalar(select(User).where(User.email == seed.email))
    if user is None:
        user = User(email=seed.email, name=seed.name, role=seed.role)
        session.add(user)
        await session.flush()

    pinned = _pinned_key(seed)
    if pinned:
        if await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(pinned))):
            return f"{seed.email:<22} {seed.role:<6} key unchanged ({display_prefix(pinned)}...)"
        session.add(
            ApiKey(
                user_id=user.id,
                name="seed",
                key_prefix=display_prefix(pinned),
                key_hash=hash_api_key(pinned),
            )
        )
        return f"{seed.email:<22} {seed.role:<6} key from SEED_{seed.env_name}_API_KEY"

    existing = await session.scalar(select(ApiKey).where(ApiKey.user_id == user.id))
    if existing is not None:
        return f"{seed.email:<22} {seed.role:<6} already has a key ({existing.key_prefix}...)"
    key = generate_api_key()
    session.add(
        ApiKey(
            user_id=user.id, name="seed", key_prefix=display_prefix(key), key_hash=hash_api_key(key)
        )
    )
    return f"{seed.email:<22} {seed.role:<6} NEW KEY (shown once): {key}"


async def main() -> None:
    db = Database.from_settings(get_settings())
    try:
        async with db.sessionmaker() as session:
            lines = [await _seed_one(session, seed) for seed in SEED_USERS]
            await session.commit()
        print("\n".join(lines))
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
