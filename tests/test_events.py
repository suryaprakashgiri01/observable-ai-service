from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.integrations.events import (
    InMemoryPublisher,
    KafkaPublisher,
    NoopPublisher,
    build_publisher,
)
from tests.conftest import make_settings, metric


def test_flag_off_builds_a_noop_publisher() -> None:
    publisher = build_publisher(make_settings(kafka_enabled=False))
    assert isinstance(publisher, NoopPublisher) and publisher.status() == "disabled"


def test_flag_on_builds_a_kafka_publisher() -> None:
    assert isinstance(build_publisher(make_settings(kafka_enabled=True)), KafkaPublisher)


class _FakeProducer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, Any, str, list[tuple[str, bytes]]]] = []
        self.fail = fail

    async def send_and_wait(self, topic: str, value: Any, *, key: str, headers: Any) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append((topic, value, key, headers))

    async def stop(self) -> None: ...


async def test_kafka_publish_success_and_failure_are_counted() -> None:
    publisher = KafkaPublisher(make_settings(kafka_enabled=True))
    publisher._producer = _FakeProducer()
    before = metric("kafka_publish_total", topic="ai-service.ask-status", outcome="success")
    await publisher.publish("ask_status", "job-1", {"status": "running"})
    sent = publisher._producer.sent[0]
    assert sent[0] == "ai-service.ask-status" and sent[2] == "job-1"
    assert sent[1]["status"] == "running" and "published_at" in sent[1]
    assert metric("kafka_publish_total", topic="ai-service.ask-status", outcome="success") == (
        before + 1
    )

    publisher._producer = _FakeProducer(fail=True)
    errors = metric("kafka_publish_total", topic="ai-service.notifications", outcome="error")
    await publisher.publish("notifications", "fp", {"x": 1})  # must not raise
    assert metric("kafka_publish_total", topic="ai-service.notifications", outcome="error") == (
        errors + 1
    )


async def test_unstarted_producer_drops_and_counts() -> None:
    publisher = KafkaPublisher(make_settings(kafka_enabled=True))
    await publisher.publish("ask_status", "job-2", {"status": "queued"})  # must not raise


def test_webhook_publishes_notifications_when_enabled(settings, span_exporter) -> None:
    from app.core.app_factory import create_app
    from tests.conftest import memory_database, prepare

    db, events = memory_database(), InMemoryPublisher()
    with TestClient(create_app(settings, db, events)) as c:
        c.portal.call(prepare, db)
        c.post(
            "/alerts/webhook",
            headers={"Authorization": "Bearer local-alert-token"},
            json={
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "Http5xxErrors", "severity": "critical"},
                        "startsAt": "2026-10-15T10:00:00Z",
                        "fingerprint": "fp-k",
                    }
                ],
            },
        )
        ready = c.get("/health/ready").json()
    kind, key, event = events.events[0]
    assert (kind, key, event["alertname"], event["status"]) == (
        "notifications",
        "fp-k",
        "Http5xxErrors",
        "firing",
    )
    assert ready["checks"]["kafka"] == "in-memory"
