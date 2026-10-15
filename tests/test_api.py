from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ANALYST_KEY, INACTIVE_KEY, auth, metric

TICKET_Q = {"question": "Create a high priority ticket: checkout page is slow"}
REPORT_Q = {"question": "Give me a revenue report for the last 7 days"}


# --- auth --------------------------------------------------------------------------


def test_missing_key_is_401_auth_required(client: TestClient) -> None:
    before = metric("auth_failures_total", reason="missing")
    response = client.post("/ask", json=TICKET_Q)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert metric("auth_failures_total", reason="missing") == before + 1


def test_unknown_and_inactive_keys_are_401_auth_invalid(client: TestClient) -> None:
    for key, reason in (("oas_nope", "unknown"), (INACTIVE_KEY, "inactive_user")):
        before = metric("auth_failures_total", reason=reason)
        response = client.get("/models", headers=auth(key))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_INVALID"
        assert metric("auth_failures_total", reason=reason) == before + 1


# --- /ask --------------------------------------------------------------------------


def test_ask_creates_a_ticket_that_can_be_read_back(client: TestClient, spans) -> None:
    before = metric("tickets_created_total", priority="high")
    response = client.post("/ask", json=TICKET_Q, headers=auth(**{"X-Request-ID": "demo-1"}))
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "create_ticket"
    assert body["request_id"] == "demo-1" and len(body["trace_id"]) == 32
    ticket = body["action"]["ticket"]
    assert ticket["priority"] == "high" and ticket["title"] == "checkout page is slow"
    assert ticket["request_id"] == "demo-1" and ticket["trace_id"] == body["trace_id"]
    assert metric("tickets_created_total", priority="high") == before + 1

    names = {s.name for s in spans.get_finished_spans()}
    assert {"model.resolve", "agent.plan", "llm.call", "tool.create_ticket"} <= names

    fetched = client.get(f"/tickets/{ticket['id']}", headers=auth())
    assert fetched.status_code == 200 and fetched.json()["id"] == ticket["id"]
    other = client.get(f"/tickets/{ticket['id']}", headers=auth(ANALYST_KEY))
    assert other.status_code == 404 and other.json()["error"]["code"] == "TICKET_NOT_FOUND"


def test_ask_generates_a_report(client: TestClient) -> None:
    body = client.post("/ask", json=REPORT_Q, headers=auth()).json()
    assert body["intent"] == "generate_report"
    report = body["action"]["report"]
    assert report["metric"] == "revenue" and report["period_days"] == 7
    assert body["answer"].startswith("Revenue for the last 7 days")
    assert body["model"] == {
        "requested": "mock-echo",
        "used": "mock-echo",
        "provider": "mock",
        "tier": "economy",
        "source": "global_default",
        "fallback_used": False,
        "attempts": 1,
        "chain": ["mock-echo"],
    }


def test_empty_question_is_422_with_request_id_and_counted(client: TestClient) -> None:
    before = metric("ask_errors_total", code="VALIDATION_ERROR")
    response = client.post(
        "/ask", json={"question": "   "}, headers=auth(**{"X-Request-ID": "bad-1"})
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR" and error["request_id"] == "bad-1"
    assert error["details"][0]["loc"] == ["body", "question"]
    assert metric("ask_errors_total", code="VALIDATION_ERROR") == before + 1


def test_unknown_model_header_is_400(client: TestClient) -> None:
    response = client.post("/ask", json=REPORT_Q, headers=auth(**{"X-Model-Id": "nope"}))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"


def test_model_without_credentials_is_400(client: TestClient) -> None:
    response = client.post("/ask", json={**REPORT_Q, "model_id": "claude-opus-5-5"}, headers=auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_header_wins_over_body(client: TestClient) -> None:
    before = metric("model_selection_total", source="header")
    response = client.post(
        "/ask",
        json={**REPORT_Q, "model_id": "does-not-exist"},
        headers=auth(**{"X-Model-Id": "mock-echo"}),
    )
    assert response.status_code == 200 and response.json()["model"]["source"] == "header"
    assert metric("model_selection_total", source="header") == before + 1


def _register_byom(client: TestClient, model_id: str = "my-byom") -> None:
    response = client.post(
        "/models/custom",
        json={
            "id": model_id,
            "name": "My endpoint",
            "base_url": "http://byom.test/v1",
            "provider_model": "llama3",
            "tier": "standard",
            "api_key": "byom-secret",
        },
        headers=auth(),
    )
    assert response.status_code == 201, response.text
    assert "byom-secret" not in response.text


def test_llm_timeout_retries_then_falls_back(client: TestClient, spans) -> None:
    _register_byom(client)
    retries = metric("llm_retries_total", model="my-byom")
    fallbacks = metric(
        "llm_fallbacks_total", from_model="my-byom", to_model="mock-echo", reason="timeout"
    )
    response = client.post(
        "/ask",
        json=REPORT_Q,
        headers=auth(**{"X-Model-Id": "my-byom", "X-Debug-Fault": "llm_timeout"}),
    )
    assert response.status_code == 200
    model = response.json()["model"]
    assert model["requested"] == "my-byom" and model["used"] == "mock-echo"
    assert model["fallback_used"] is True and model["attempts"] == 4
    assert metric("llm_retries_total", model="my-byom") == retries + 2
    assert (
        metric("llm_fallbacks_total", from_model="my-byom", to_model="mock-echo", reason="timeout")
        == fallbacks + 1
    )
    attempts = [s for s in spans.get_finished_spans() if s.name == "llm.call"]
    assert [s.attributes["llm.model_id"] for s in attempts] == ["my-byom"] * 3 + ["mock-echo"]


def test_tool_failure_is_502(client: TestClient) -> None:
    before = metric("agent_tool_calls_total", tool="create_ticket", outcome="error")
    response = client.post("/ask", json=TICKET_Q, headers=auth(**{"X-Debug-Fault": "tool_error"}))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "TOOL_FAILED"
    assert metric("agent_tool_calls_total", tool="create_ticket", outcome="error") == before + 1


def test_fault_header_is_ignored_when_injection_is_disabled(settings, span_exporter) -> None:
    from app.core.app_factory import create_app
    from tests.conftest import memory_database, prepare

    db = memory_database()
    app = create_app(settings.model_copy(update={"enable_fault_injection": False}), db)
    with TestClient(app) as c:
        c.portal.call(prepare, db)
        response = c.post("/ask", json=TICKET_Q, headers=auth(**{"X-Debug-Fault": "tool_error"}))
    assert response.status_code == 200


# --- /models -------------------------------------------------------------------------


def test_models_lists_catalog_by_tier_with_flags(client: TestClient) -> None:
    body = client.get("/models", headers=auth()).json()
    assert body["total"] == 10
    assert body["default_model_id"] == "mock-echo"
    by_id = {m["id"]: m for m in body["items"]}
    assert by_id["claude-haiku-5-5"] == {
        "id": "claude-haiku-5-5",
        "name": "Claude Haiku 5.5",
        "provider": "anthropic",
        "tier": "economy",
        "available": False,
        "is_default": False,
        "is_fallback": False,
        "owned": False,
    }
    assert by_id["mock-echo"]["available"] is True
    premium = client.get("/models", params={"tier": "premium"}, headers=auth()).json()
    assert {m["tier"] for m in premium["items"]} == {"premium"} and premium["total"] == 3


def test_default_model_is_per_user(client: TestClient) -> None:
    _register_byom(client)
    response = client.put("/models/default", json={"model_id": "my-byom"}, headers=auth())
    assert response.status_code == 200 and response.json()["default_model_id"] == "my-byom"
    mine = client.get("/models", headers=auth()).json()
    theirs = client.get("/models", headers=auth(ANALYST_KEY)).json()
    assert mine["default_model_id"] == "my-byom"
    assert theirs["default_model_id"] == "mock-echo"


def test_cannot_set_unavailable_model_as_fallback(client: TestClient) -> None:
    response = client.put("/models/fallback", json={"model_id": "gpt-4.1"}, headers=auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    ok = client.put("/models/fallback", json={"model_id": "mock-echo"}, headers=auth())
    assert ok.json()["fallback_model_id"] == "mock-echo"


def test_custom_model_conflict_and_delete(client: TestClient) -> None:
    _register_byom(client, "shared-name")
    clash = client.post(
        "/models/custom",
        json={
            "id": "shared-name",
            "name": "x",
            "base_url": "http://other.test/v1",
            "provider_model": "m",
        },
        headers=auth(ANALYST_KEY),
    )
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "MODEL_CONFLICT"
    assert client.delete("/models/custom/shared-name", headers=auth(ANALYST_KEY)).status_code == 400
    assert client.delete("/models/custom/shared-name", headers=auth()).status_code == 204
    listed = client.get("/models", headers=auth()).json()
    assert all(m["id"] != "shared-name" for m in listed["items"])


def test_custom_model_id_is_validated(client: TestClient) -> None:
    response = client.post(
        "/models/custom",
        json={"id": "Bad Id!", "name": "x", "base_url": "not-a-url", "provider_model": "m"},
        headers=auth(),
    )
    assert response.status_code == 422
    locs = {tuple(e["loc"]) for e in response.json()["error"]["details"]}
    assert ("body", "id") in locs and ("body", "base_url") in locs


def test_ticket_ids_are_uuids(client: TestClient) -> None:
    import uuid

    body = client.post("/ask", json=TICKET_Q, headers=auth()).json()
    ticket_id = body["action"]["ticket"]["id"]
    assert uuid.UUID(ticket_id).version == 4
    assert ticket_id in body["answer"]
    missing = client.get(f"/tickets/{uuid.uuid4()}", headers=auth())
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "TICKET_NOT_FOUND"
    assert client.get("/tickets/12345", headers=auth()).status_code == 422
