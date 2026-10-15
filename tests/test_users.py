from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_KEY, ANALYST_KEY, DEMO_KEY, auth, metric


def test_me_returns_the_caller(client: TestClient) -> None:
    r = client.get("/me", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert (body["email"], body["role"]) == ("demo@example.com", "user")
    assert body["key_prefix"] == DEMO_KEY[:10]
    assert DEMO_KEY not in r.text


def test_me_without_or_with_a_bad_key_is_401(client: TestClient) -> None:
    assert client.get("/me").json()["error"]["code"] == "AUTH_REQUIRED"
    assert client.get("/me", headers=auth("oas_nope")).json()["error"]["code"] == "AUTH_INVALID"


def test_admin_users_is_403_for_users_and_200_for_admins(client: TestClient) -> None:
    before = metric("auth_failures_total", reason="forbidden")
    forbidden = client.get("/admin/users", headers=auth())
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "FORBIDDEN"
    assert metric("auth_failures_total", reason="forbidden") == before + 1

    ok = client.get("/admin/users", headers=auth(ADMIN_KEY))
    assert ok.status_code == 200
    users = {u["email"]: u for u in ok.json()["items"]}
    assert users["admin@example.com"]["role"] == "admin"
    assert users["gone@example.com"]["is_active"] is False
    assert users["demo@example.com"]["api_keys"] == 1
    assert users["demo@example.com"]["last_used_at"] is not None  # used just above

    assert client.get("/admin/users").status_code == 401


def test_tickets_list_is_scoped_and_newest_first(client: TestClient) -> None:
    for title in ("first issue", "second issue"):
        client.post("/ask", json={"question": f"Create a ticket: {title}"}, headers=auth())
    client.post(
        "/ask", json={"question": "Create a ticket: analyst issue"}, headers=auth(ANALYST_KEY)
    )

    mine = client.get("/tickets", headers=auth()).json()
    assert [t["title"] for t in mine["items"]] == ["second issue", "first issue"]
    assert mine["total"] == 2
    limited = client.get("/tickets", params={"limit": 1}, headers=auth()).json()
    assert len(limited["items"]) == 1
    assert client.get("/tickets", params={"limit": 0}, headers=auth()).status_code == 422
