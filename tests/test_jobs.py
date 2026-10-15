from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.app_factory import create_app
from app.integrations.events import InMemoryPublisher
from tests.conftest import ANALYST_KEY, auth, memory_database, prepare

ASYNC = {"Prefer": "respond-async"}
REPORT = {"question": "Give me a revenue report for the last 7 days"}


@pytest.fixture
def events() -> InMemoryPublisher:
    return InMemoryPublisher()


@pytest.fixture
def jobs_client(settings, span_exporter, events) -> Iterator[TestClient]:
    db = memory_database()
    chained = settings.model_copy(update={"llm_fallback_chain": "mock-standard,mock-economy"})
    with TestClient(create_app(chained, db, events)) as c:
        c.portal.call(prepare, db)
        yield c


def _wait(client: TestClient, url: str, key: str | None = None) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(url, headers=auth(key) if key else auth()).json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_async_ask_returns_202_then_completes(jobs_client: TestClient, events) -> None:
    r = jobs_client.post("/ask", json=REPORT, headers=auth(**ASYNC, **{"X-Request-ID": "a-1"}))
    assert r.status_code == 202
    accepted = r.json()
    assert accepted["status"] == "queued" and accepted["request_id"] == "a-1"
    assert r.headers["Location"] == accepted["status_url"]

    job = _wait(jobs_client, accepted["status_url"])
    assert job["status"] == "completed" and job["error"] is None
    assert job["result"]["intent"] == "generate_report"
    assert job["result"]["model"]["used"] == "mock-echo"
    assert [e["status"] for e in job["events"]] == ["queued", "running", "completed"]
    assert job["trace_id"] == accepted["trace_id"]

    published = [(k, e["status"]) for k, _, e in events.events if k == "ask_status"]
    assert published == [
        ("ask_status", "queued"),
        ("ask_status", "running"),
        ("ask_status", "completed"),
    ]


def test_async_multi_model_timeout_reports_every_transition(jobs_client, events) -> None:
    headers = auth(
        **ASYNC,
        **{
            "X-Model-Id": "mock-premium",
            "X-Debug-Fault": "llm_timeout",
            "X-Debug-Fault-Count": "2",
        },
    )
    url = jobs_client.post("/ask", json=REPORT, headers=headers).json()["status_url"]
    job = _wait(jobs_client, url)
    statuses = [e["status"] for e in job["events"]]
    assert statuses[0] == "queued" and statuses[-1] == "completed"
    assert statuses.count("retrying") == 4 and statuses.count("fallback") == 2
    assert job["attempts"] == 7 and job["current_model"] == "mock-economy"
    assert job["result"]["model"]["chain"] == ["mock-premium", "mock-standard", "mock-economy"]
    kafka = [e["status"] for k, _, e in events.events if k == "ask_status"]
    assert kafka == statuses


def test_async_tool_failure_ends_failed(jobs_client) -> None:
    url = jobs_client.post(
        "/ask",
        json={"question": "Create a ticket: search is broken"},
        headers=auth(**ASYNC, **{"X-Debug-Fault": "tool_error"}),
    ).json()["status_url"]
    job = _wait(jobs_client, url)
    assert job["status"] == "failed" and job["error"]["code"] == "TOOL_FAILED"
    assert job["result"] is None


def test_bad_model_is_rejected_before_queueing(jobs_client, events) -> None:
    r = jobs_client.post("/ask", json=REPORT, headers=auth(**ASYNC, **{"X-Model-Id": "nope"}))
    assert r.status_code == 400 and r.json()["error"]["code"] == "MODEL_NOT_FOUND"
    assert events.events == []


def test_jobs_are_private_and_unknown_is_404(jobs_client) -> None:
    url = jobs_client.post("/ask", json=REPORT, headers=auth(**ASYNC)).json()["status_url"]
    _wait(jobs_client, url)
    other = jobs_client.get(url, headers=auth(ANALYST_KEY))
    assert other.status_code == 404 and other.json()["error"]["code"] == "JOB_NOT_FOUND"
    assert jobs_client.get(f"/ask/jobs/{uuid.uuid4()}", headers=auth()).status_code == 404


def test_interrupted_jobs_are_failed_on_startup(jobs_client) -> None:
    from sqlalchemy import select

    from app.db.models import AskJob, User

    app = jobs_client.app

    async def leave_running() -> uuid.UUID:
        async with app.state.db.sessionmaker() as s:
            user_id = (await s.scalars(select(User.id).limit(1))).one()
            job = AskJob(
                user_id=user_id,
                question="q",
                model_requested="mock-echo",
                status="running",
                events=[],
            )
            s.add(job)
            await s.commit()
            return job.id

    job_id = jobs_client.portal.call(leave_running)
    assert jobs_client.portal.call(app.state.jobs.recover_interrupted) == 1

    async def status() -> tuple[str, dict]:
        async with app.state.db.sessionmaker() as s:
            row = await s.get(AskJob, job_id)
            return row.status, row.error

    assert jobs_client.portal.call(status) == (
        "failed",
        {"code": "INTERRUPTED", "message": "Service restarted mid-job"},
    )
