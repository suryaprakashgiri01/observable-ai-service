from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import (
    _add_trace_context,
    _redact_secrets,
    resolve_log_format,
    setup_logging,
)
from app.core.telemetry import tracer
from app.middleware.request_context import RequestContextMiddleware, resolve_request_id
from tests.conftest import metric


def test_request_id_accepts_safe_values_and_replaces_unsafe_ones() -> None:
    assert resolve_request_id("demo-1") == "demo-1"
    generated = resolve_request_id("bad id\nInjected: yes")
    assert generated != "bad id\nInjected: yes" and len(generated) == 32
    assert len(resolve_request_id(None)) == 32
    assert len(resolve_request_id("x" * 200)) == 32


def test_secrets_are_redacted_from_log_events() -> None:
    event = _redact_secrets(None, "info", {"event": "x", "api_key": "sk-1", "X-API-Key": "k"})
    assert event["api_key"] == "[REDACTED]" and event["X-API-Key"] == "[REDACTED]"


def test_trace_ids_are_stamped_only_inside_a_span() -> None:
    assert "trace_id" not in _add_trace_context(None, "info", {"event": "x"})
    with tracer.start_as_current_span("t"):
        event = _add_trace_context(None, "info", {"event": "x"})
    assert len(event["trace_id"]) == 32 and len(event["span_id"]) == 16


def test_log_format_explicit_setting_wins() -> None:
    assert resolve_log_format("json") == "json"
    assert resolve_log_format("console") == "console"


def test_health_needs_no_auth_and_echoes_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "probe-1"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"] == "probe-1"


def test_readiness_reports_database_and_providers(client: TestClient) -> None:
    body = client.get("/health/ready").json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["providers"]["mock"] is True


def test_readiness_is_503_when_database_is_unreachable(settings) -> None:
    from app.core.app_factory import create_app
    from app.db.session import Database

    bad = Database.from_settings(
        settings.model_copy(update={"database_url": "sqlite+aiosqlite:////nonexistent/dir/x.db"})
    )
    with TestClient(create_app(settings, bad)) as broken:
        response = broken.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


def test_metrics_exposes_every_metric_family(client: TestClient) -> None:
    client.get("/health")
    text = client.get("/metrics").text
    for family in (
        "http_requests_total",
        "http_request_duration_seconds",
        "http_requests_in_progress",
        "ask_requests_total",
        "ask_errors_total",
        "llm_requests_total",
        "llm_request_duration_seconds",
        "llm_tokens_total",
        "llm_retries_total",
        "llm_fallbacks_total",
        "model_selection_total",
        "agent_tool_calls_total",
        "tickets_created_total",
        "auth_failures_total",
    ):
        assert f"# TYPE {family}" in text, family


def test_http_metrics_use_route_templates(client: TestClient) -> None:
    before = metric("http_requests_total", method="GET", route="/tickets/{ticket_id}", status="401")
    client.get("/tickets/12345")
    after = metric("http_requests_total", method="GET", route="/tickets/{ticket_id}", status="401")
    assert after == before + 1


def test_unknown_route_renders_the_error_envelope(client: TestClient) -> None:
    response = client.get("/nope", headers={"X-Request-ID": "lost-1"})
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Not Found",
        "request_id": "lost-1",
    }


def test_unhandled_exception_keeps_request_id_and_envelope() -> None:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get("/boom", headers={"X-Request-ID": "boom-1"})
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "boom-1"
    body = response.json()["error"]
    assert body["code"] == "INTERNAL_ERROR" and body["request_id"] == "boom-1"
    assert "kaboom" not in response.text


def test_every_log_line_of_a_request_carries_its_request_id(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    # Re-bind the log handler to the stream capsys is capturing.
    setup_logging(level="INFO", log_format="json")
    capsys.readouterr()
    client.post(
        "/ask",
        json={"question": "Revenue for the last 7 days"},
        headers={"X-API-Key": "oas_test_demo_key", "X-Request-ID": "trace-me-1"},
    )
    lines = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    ours = [line for line in lines if line.get("logger", "").startswith("app")]
    assert ours, "expected app log lines"
    assert all(line.get("request_id") == "trace-me-1" for line in ours)
    access = next(line for line in ours if line["event"] == "request completed")
    assert access["route"] == "/ask" and access["status"] == 200
    assert len(access["trace_id"]) == 32
    assert any(line.get("user_id") for line in ours)


def test_bounded_series_exist_at_zero_before_any_event(client: TestClient) -> None:
    """A series born at 1 is invisible to rate()/increase(); pre-created ones are not."""
    text = client.get("/metrics").text
    for line in (
        'http_requests_total{method="POST",route="/ask",status="502"}',
        'http_requests_total{method="GET",route="/models",status="403"}',
        'ask_errors_total{code="TOOL_FAILED"}',
        'auth_failures_total{reason="revoked"}',
        'agent_tool_calls_total{outcome="error",tool="generate_report"}',
    ):
        assert line in text, line
    assert "# TYPE python_gc_collections_total counter" in text


def test_langfuse_base_url_is_read_under_both_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    assert Settings(_env_file=None).langfuse_host == "https://us.cloud.langfuse.com"  # type: ignore[call-arg]
    monkeypatch.delenv("LANGFUSE_BASE_URL")
    monkeypatch.setenv("LANGFUSE_HOST", "https://eu.example")
    assert Settings(_env_file=None).langfuse_host == "https://eu.example"  # type: ignore[call-arg]
