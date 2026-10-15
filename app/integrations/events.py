"""Event fan-out to Kafka, behind the ``KAFKA_ENABLED`` feature flag.

Kafka is an optional copy, never the source of truth: notifications and job
status are already in Postgres. So a publish failure is logged and counted,
and never fails the request or the job.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import structlog
from opentelemetry import propagate

from app.core.config import Settings
from app.core.context import get_request_id, get_session_id
from app.core.metrics import KAFKA_PUBLISH

logger = structlog.get_logger(__name__)

EventKind = Literal["notifications", "ask_status"]


class EventPublisher(Protocol):
    enabled: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def publish(self, kind: EventKind, key: str, event: dict[str, Any]) -> None: ...

    def status(self) -> str: ...


class NoopPublisher:
    """KAFKA_ENABLED=false: every publish is a no-op, nothing connects."""

    enabled = False

    async def start(self) -> None:
        logger.info("kafka disabled (KAFKA_ENABLED=false)")

    async def stop(self) -> None:
        return None

    async def publish(self, kind: EventKind, key: str, event: dict[str, Any]) -> None:
        return None

    def status(self) -> str:
        return "disabled"


class InMemoryPublisher:
    """Collects events; used by tests and handy for local debugging."""

    enabled = True

    def __init__(self) -> None:
        self.events: list[tuple[EventKind, str, dict[str, Any]]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, kind: EventKind, key: str, event: dict[str, Any]) -> None:
        self.events.append((kind, key, event))

    def status(self) -> str:
        return "in-memory"


def _headers() -> list[tuple[str, bytes]]:
    """Correlation for consumers: request/session ids and the W3C trace context."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    values = {"request_id": get_request_id(), "session_id": get_session_id(), **carrier}
    return [(k, v.encode()) for k, v in values.items() if v]


class KafkaPublisher:
    enabled = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._topics: dict[EventKind, str] = {
            "notifications": settings.kafka_topic_notifications,
            "ask_status": settings.kafka_topic_ask_status,
        }
        self._producer: Any | None = None
        self._state = "starting"

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            client_id="observable-ai-service",
            acks=1,
            linger_ms=5,
            request_timeout_ms=int(self._settings.kafka_publish_timeout_seconds * 1000),
            key_serializer=lambda k: k.encode(),
            value_serializer=lambda v: json.dumps(v, default=str).encode(),
        )
        try:
            await asyncio.wait_for(
                producer.start(), timeout=self._settings.kafka_publish_timeout_seconds
            )
        except Exception as exc:
            # Degrade instead of refusing to boot: Kafka is optional fan-out.
            self._state = "unavailable"
            logger.error(
                "kafka unavailable; events will be dropped and counted",
                bootstrap=self._settings.kafka_bootstrap_servers,
                error=type(exc).__name__,
            )
            await producer.stop()
            return
        self._producer = producer
        self._state = "ok"
        logger.info("kafka producer started", bootstrap=self._settings.kafka_bootstrap_servers)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, kind: EventKind, key: str, event: dict[str, Any]) -> None:
        topic = self._topics[kind]
        if self._producer is None:
            KAFKA_PUBLISH.labels(topic=topic, outcome="error").inc()
            return
        payload = {**event, "published_at": datetime.now(UTC).isoformat()}
        try:
            await asyncio.wait_for(
                self._producer.send_and_wait(topic, payload, key=key, headers=_headers()),
                timeout=self._settings.kafka_publish_timeout_seconds,
            )
        except Exception as exc:
            KAFKA_PUBLISH.labels(topic=topic, outcome="error").inc()
            logger.warning("kafka publish failed", topic=topic, key=key, error=type(exc).__name__)
            return
        KAFKA_PUBLISH.labels(topic=topic, outcome="success").inc()
        logger.debug("kafka event published", topic=topic, key=key)

    def status(self) -> str:
        return self._state


def build_publisher(settings: Settings) -> EventPublisher:
    return KafkaPublisher(settings) if settings.kafka_enabled else NoopPublisher()
