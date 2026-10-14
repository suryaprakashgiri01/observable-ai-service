from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY as _UNUSED  # noqa: F401 - keep default registry importable
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.auth.keys import display_prefix, hash_api_key
from app.core.app_factory import create_app
from app.core.config import Settings
from app.core.metrics import REGISTRY
from app.core.telemetry import configure_tracing
from app.db.models import ApiKey, Base, User
from app.db.session import Database
from app.llm.registry import sync_catalog

DEMO_KEY = "oas_test_demo_key"
ANALYST_KEY = "oas_test_analyst_key"
INACTIVE_KEY = "oas_test_inactive_key"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "database_url": "sqlite+aiosqlite://",
        "log_format": "json",
        "enable_fault_injection": True,
        "fault_injection_delay_seconds": 0,
        "llm_backoff_base_seconds": 0,
        "llm_max_attempts": 3,
        "llm_default_model_id": "mock-echo",
        "llm_fallback_model_id": "mock-echo",
        "app_encryption_key": Fernet.generate_key().decode(),
        "openai_api_key": "",
        "anthropic_api_key": "",
        "gemini_api_key": "",
        "langfuse_public_key": "",
        "otel_exporter_otlp_endpoint": "",
        "auto_sync_catalog": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(scope="session", autouse=True)
def span_exporter() -> InMemorySpanExporter:
    configure_tracing(make_settings())
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture
def spans(span_exporter: InMemorySpanExporter) -> InMemorySpanExporter:
    span_exporter.clear()
    return span_exporter


def memory_database() -> Database:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    return Database(engine)


async def prepare(db: Database) -> None:
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db.sessionmaker() as session:
        await sync_catalog(session)
        for email, name, key, active in (
            ("demo@example.com", "Demo", DEMO_KEY, True),
            ("analyst@example.com", "Analyst", ANALYST_KEY, True),
            ("gone@example.com", "Gone", INACTIVE_KEY, False),
        ):
            user = User(email=email, name=name, role="user", is_active=active)
            session.add(user)
            await session.flush()
            session.add(
                ApiKey(user_id=user.id, key_prefix=display_prefix(key), key_hash=hash_api_key(key))
            )
        await session.commit()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings, span_exporter: InMemorySpanExporter) -> Iterator[TestClient]:
    db = memory_database()
    app = create_app(settings, db)
    with TestClient(app) as test_client:
        test_client.portal.call(prepare, db)
        yield test_client


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    db = memory_database()
    await prepare(db)
    async with db.sessionmaker() as s:
        yield s
    await db.dispose()


def auth(key: str = DEMO_KEY, **extra: str) -> dict[str, str]:
    return {"X-API-Key": key, **extra}


def metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0
