from __future__ import annotations

from fastapi.testclient import TestClient

from app.middleware.security_headers import CONSOLE_CSP


def test_console_is_served_without_a_key_with_security_headers(client: TestClient) -> None:
    page = client.get("/ui/")
    assert page.status_code == 200
    assert "AI Service Console" in page.text
    assert page.headers["content-security-policy"] == CONSOLE_CSP
    assert page.headers["x-content-type-options"] == "nosniff"
    assert 'id="theme-toggle"' in page.text and "js/theme-boot.js" in page.text
    for asset in (
        "/ui/app.css",
        "/ui/js/main.js",
        "/ui/js/panels/ask.js",
        "/ui/js/theme.js",
        "/ui/js/theme-boot.js",
    ):
        assert client.get(asset).status_code == 200, asset


def test_root_redirects_to_the_console(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/"


def test_console_config_is_public_and_has_no_secrets(client: TestClient, settings) -> None:
    r = client.get("/ui/config")
    assert r.status_code == 200
    body = r.json()
    assert body["fault_injection"] is True and body["kafka_enabled"] is False
    assert set(body["links"]) >= {"swagger", "grafana", "jaeger", "prometheus", "alertmanager"}
    text = r.text
    for secret in (
        settings.app_encryption_key.get_secret_value(),
        settings.alert_webhook_token.get_secret_value(),
    ):
        assert secret not in text


def test_console_assets_are_not_traced(client: TestClient, spans) -> None:
    client.get("/ui/js/main.js")
    client.get("/health")
    assert not any(
        "/ui" in (s.attributes or {}).get("http.target", "") or "/ui" in s.name
        for s in spans.get_finished_spans()
    )


def test_api_routes_are_not_shadowed_by_the_static_mount(client: TestClient) -> None:
    assert client.get("/ui/config").headers["content-type"].startswith("application/json")
    assert client.get("/ui/nope.js").status_code == 404
