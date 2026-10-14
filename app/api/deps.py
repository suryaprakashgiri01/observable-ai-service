"""FastAPI dependencies shared by every router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import Database


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _database(request: Request) -> Database:
    database: Database = request.app.state.db
    return database


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.db
    async with database.sessionmaker() as session:
        yield session


AppSettings = Annotated[Settings, Depends(_settings)]
Db = Annotated[Database, Depends(_database)]
Session = Annotated[AsyncSession, Depends(_session)]
