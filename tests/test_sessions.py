from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.llm.langfuse_recorder import LangfuseRecorder
from tests.conftest import auth

REPORT_Q = {"question": "Revenue for the last 7 days"}


def test_session_defaults_to_the_request_id(client: TestClient) -> None:
    response = client.post("/ask", json=REPORT_Q, headers=auth(**{"X-Request-ID": "first-1"}))
    assert response.json()["session_id"] == "first-1"
    assert response.headers["X-Session-ID"] == "first-1"


def test_explicit_session_groups_requests_and_tags_spans(client: TestClient, spans) -> None:
    ids = set()
    for rid in ("s-a", "s-b"):
        r = client.post(
            "/ask",
            json=REPORT_Q,
            headers=auth(**{"X-Request-ID": rid, "X-Session-ID": "demo-session-1"}),
        )
        assert r.headers["X-Session-ID"] == "demo-session-1"
        assert r.json()["request_id"] == rid
        ids.add(r.json()["session_id"])
    assert ids == {"demo-session-1"}
    servers = [s for s in spans.get_finished_spans() if s.name == "POST /ask"]
    assert {s.attributes["session.id"] for s in servers} == {"demo-session-1"}


def test_reusing_a_request_id_joins_its_session(client: TestClient) -> None:
    first = client.post("/ask", json=REPORT_Q, headers=auth()).json()
    again = client.post(
        "/ask", json=REPORT_Q, headers=auth(**{"X-Session-ID": first["request_id"]})
    ).json()
    assert again["session_id"] == first["session_id"] == first["request_id"]


def test_invalid_session_id_falls_back_to_request_id(client: TestClient) -> None:
    r = client.post(
        "/ask", json=REPORT_Q, headers=auth(**{"X-Request-ID": "r-1", "X-Session-ID": "bad id!"})
    )
    assert r.json()["session_id"] == "r-1"


def test_authorize_scheme_key_alone_authenticates(client: TestClient) -> None:
    # Swagger's Authorize sends the same header; the dependency accepts it either way.
    assert client.get("/models", headers=auth()).status_code == 200


class _Obs:
    def __init__(self, log: list[tuple[str, dict[str, Any]]], kind: str) -> None:
        self.log, self.kind = log, kind

    def update(self, **kw: Any) -> None:
        self.log.append((f"{self.kind}.update", kw))

    def update_trace(self, **kw: Any) -> None:
        self.log.append(("trace.update", kw))

    def start_generation(self, **kw: Any) -> _Obs:
        self.log.append(("generation.start", kw))
        return _Obs(self.log, "generation")

    def end(self) -> None:
        self.log.append((f"{self.kind}.end", {}))


class _FakeLangfuse:
    def __init__(self) -> None:
        self.log: list[tuple[str, dict[str, Any]]] = []

    def start_span(self, **kw: Any) -> _Obs:
        self.log.append(("span.start", kw))
        return _Obs(self.log, "span")

    def start_generation(self, **kw: Any) -> _Obs:
        self.log.append(("orphan.generation", kw))
        return _Obs(self.log, "generation")

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


def test_langfuse_trace_has_user_session_and_nested_generations(client: TestClient) -> None:
    fake = _FakeLangfuse()
    recorder = LangfuseRecorder(fake)
    client.app.state.langfuse = recorder
    client.app.state.orchestrator._recorder = recorder

    r = client.post(
        "/ask",
        json={"question": "Create a high priority ticket: login is down"},
        headers=auth(**{"X-Session-ID": "lf-session"}),
    )
    assert r.status_code == 200
    events = [name for name, _ in fake.log]
    assert events[0] == "span.start" and "orphan.generation" not in events
    trace_update = next(
        kw for name, kw in fake.log if name == "trace.update" and "session_id" in kw
    )
    assert trace_update["name"] == "ask" and trace_update["session_id"] == "lf-session"
    assert trace_update["user_id"]
    gen = next(kw for name, kw in fake.log if name == "generation.start")
    assert gen["name"] == "llm.call mock-echo #1" and gen["metadata"]["session_id"] == "lf-session"
    tags = next(kw for name, kw in fake.log if name == "trace.update" and "tags" in kw)["tags"]
    assert tags == ["create_ticket", "mock-echo"]
    assert events[-1] == "span.end"
