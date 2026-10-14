"""Upsert the built-in model catalog. Idempotent: ``python -m scripts.seed_models``."""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.db.session import Database
from app.llm.catalog import CATALOG
from app.llm.registry import sync_catalog


async def main() -> None:
    db = Database.from_settings(get_settings())
    try:
        async with db.sessionmaker() as session:
            changed = await sync_catalog(session)
        print(f"model catalog: {len(CATALOG)} models, {changed} inserted or updated")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
