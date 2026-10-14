from __future__ import annotations

from fastapi.testclient import TestClient


def _params(spec: dict, path: str, method: str) -> dict[str, dict]:
    return {p["name"]: p for p in spec["paths"][path][method].get("parameters", [])}


def test_common_headers_are_documented_on_every_route(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for path, methods in spec["paths"].items():
        for method in methods:
            names = _params(spec, path, method)
            assert "X-Request-ID" in names, (method, path)
            assert "traceparent" in names, (method, path)


def test_ask_documents_model_and_fault_headers_with_examples(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    params = _params(spec, "/ask", "post")
    assert {"claude-haiku-5-5", "mock-echo"} <= {
        e["value"] for e in params["X-Model-Id"]["examples"].values()
    }
    assert "llm_timeout" in {e["value"] for e in params["X-Debug-Fault"]["examples"].values()}
    body = spec["paths"]["/ask"]["post"]["requestBody"]["content"]["application/json"]
    assert {"ticket", "revenue", "invalid"} <= set(body["examples"])


def test_api_key_is_a_header_field_on_protected_routes_only(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    public = {"/health", "/health/ready", "/metrics"}
    for path, methods in spec["paths"].items():
        for method in methods:
            params = _params(spec, path, method)
            assert ("X-API-Key" in params) == (path not in public), (method, path)
    assert "make seed" in _params(spec, "/ask", "post")["X-API-Key"]["description"]


def test_error_examples(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    responses = spec["paths"]["/ask"]["post"]["responses"]
    assert {"400", "401", "422", "500", "502", "503"} <= set(responses)
    assert set(responses["401"]["content"]["application/json"]["examples"]) == {
        "AUTH_REQUIRED",
        "AUTH_INVALID",
    }
    assert set(responses["400"]["content"]["application/json"]["examples"]) == {
        "MODEL_NOT_FOUND",
        "MODEL_UNAVAILABLE",
    }


def test_documented_examples_match_real_behaviour(client: TestClient) -> None:
    """The headers are documentation only; behaviour must be unchanged."""
    response = client.get("/health", headers={"X-Request-ID": "docs-1"})
    assert response.status_code == 200 and response.headers["X-Request-ID"] == "docs-1"


def test_authorize_button_and_session_header_are_documented(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    scheme = spec["components"]["securitySchemes"]["ApiKeyAuth"]
    assert (scheme["type"], scheme["in"], scheme["name"]) == ("apiKey", "header", "X-API-Key")
    assert spec["paths"]["/ask"]["post"]["security"] == [{"ApiKeyAuth": []}]
    assert "X-Session-ID" in _params(spec, "/models", "get")
