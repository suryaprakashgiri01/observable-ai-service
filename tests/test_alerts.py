from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import auth, metric

HOOK = {"Authorization": "Bearer local-alert-token"}


def _payload(status: str, *, alertname: str = "Http5xxErrors", fp: str = "fp-1") -> dict:
    ends = "2026-10-15T10:05:00Z" if status == "resolved" else "0001-01-01T00:00:00Z"
    return {
        "version": "4",
        "status": status,
        "receiver": "in-app",
        "alerts": [
            {
                "status": status,
                "labels": {"alertname": alertname, "severity": "critical", "route": "/ask"},
                "annotations": {"summary": "5xx on /ask", "description": "1 x 502"},
                "startsAt": "2026-10-15T10:00:00Z",
                "endsAt": ends,
                "fingerprint": fp,
            }
        ],
    }


def test_webhook_rejects_a_bad_token(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}):
        r = client.post("/alerts/webhook", json=_payload("firing"), headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "WEBHOOK_UNAUTHORIZED"


def test_firing_then_resolved_updates_one_notification(client: TestClient) -> None:
    before = metric("alerts_received_total", alertname="Http5xxErrors", status="firing")
    assert client.post("/alerts/webhook", json=_payload("firing"), headers=HOOK).json() == {
        "received": 1
    }
    assert metric("alerts_received_total", alertname="Http5xxErrors", status="firing") == before + 1

    listed = client.get("/notifications", headers=auth()).json()
    assert listed["unread"] == 1
    item = listed["items"][0]
    assert (item["alertname"], item["status"], item["severity"]) == (
        "Http5xxErrors",
        "firing",
        "critical",
    )
    assert item["ends_at"] is None

    client.post("/alerts/webhook", json=_payload("resolved"), headers=HOOK)
    listed = client.get("/notifications", headers=auth()).json()
    assert len(listed["items"]) == 1 and listed["items"][0]["status"] == "resolved"
    assert listed["items"][0]["ends_at"] is not None


def test_filters_and_mark_read(client: TestClient) -> None:
    client.post("/alerts/webhook", json=_payload("firing", fp="a"), headers=HOOK)
    client.post(
        "/alerts/webhook",
        json=_payload("firing", alertname="Http4xxAuthErrors", fp="b"),
        headers=HOOK,
    )
    items = client.get("/notifications", params={"unread": "true"}, headers=auth()).json()["items"]
    assert {i["alertname"] for i in items} == {"Http5xxErrors", "Http4xxAuthErrors"}

    read = client.post(f"/notifications/{items[0]['id']}/read", headers=auth())
    assert read.status_code == 200 and read.json()["read_at"] is not None
    left = client.get("/notifications", params={"unread": "true"}, headers=auth()).json()
    assert left["unread"] == 1 and len(left["items"]) == 1
    assert (
        client.get("/notifications", params={"status": "resolved"}, headers=auth()).json()["items"]
        == []
    )


def test_mark_read_unknown_is_404_and_list_needs_a_key(client: TestClient) -> None:
    r = client.post(f"/notifications/{uuid.uuid4()}/read", headers=auth())
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOTIFICATION_NOT_FOUND"
    assert client.get("/notifications").status_code == 401
