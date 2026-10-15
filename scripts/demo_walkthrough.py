"""Guided API walkthrough for the demo, with coloured logs.

Every step logs the request it sends, the response it gets, why it matters, and
where to look (Jaeger trace, Grafana panel, Prometheus query, Langfuse session,
Alertmanager, Kafka UI).

    uv run python -m scripts.demo_walkthrough            # run straight through
    uv run python -m scripts.demo_walkthrough --pause    # press Enter between steps
    make demo ARGS="--pause --session my-take-2"
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from scripts._common import Config, console_logger

log = console_logger("demo")

DASH = "/d/observable-ai-service/observable-ai-service"
REPORT = {"question": "Give me a revenue report for the last 7 days"}


class Walkthrough:
    def __init__(self, cfg: Config, session: str, pause: bool, wait_alerts: int) -> None:
        self.cfg = cfg
        self.session = session
        self.pause = pause
        self.wait_alerts = wait_alerts
        self.http = httpx.Client(base_url=cfg.base_url, timeout=60)
        self.step_no = 0
        self.ticket_id: str | None = None
        self.langfuse_project = self._langfuse_project()

    # --- output helpers -----------------------------------------------------------
    def step(self, title: str) -> None:
        self.step_no += 1
        if self.pause and self.step_no > 1:
            input("\n  ⏎  press Enter for the next step ")
        print()
        log.info(f"━━━━ STEP {self.step_no} · {title} ━━━━")

    def why(self, *lines: str) -> None:
        for line in lines:
            log.info("why", note=line)

    def link(self, label: str, url: str) -> None:
        log.info("open", what=label, url=url)

    def grafana(self, panel: int, label: str) -> None:
        self.link(
            f"Grafana · {label}", f"{self.cfg.grafana}{DASH}?viewPanel={panel}&from=now-15m&to=now"
        )

    def prom(self, label: str, expr: str) -> None:
        self.link(
            f"Prometheus · {label}",
            f"{self.cfg.prometheus}/graph?g0.expr={quote(expr)}&g0.tab=1&g0.range_input=15m",
        )

    def jaeger(self, trace_id: str | None) -> None:
        if trace_id:
            self.link("Jaeger · this request's trace", f"{self.cfg.jaeger}/trace/{trace_id}")

    def langfuse(self) -> None:
        if self.langfuse_project:
            self.link(
                "Langfuse · this demo's session",
                f"{self.cfg.langfuse_host}/project/{self.langfuse_project}/sessions/{self.session}",
            )

    # --- HTTP ---------------------------------------------------------------------
    def call(
        self,
        method: str,
        path: str,
        *,
        key: bool = True,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> httpx.Response:
        sent = {"X-Session-ID": self.session, **(headers or {})}
        if request_id:
            sent["X-Request-ID"] = request_id
        shown = dict(sent)
        if key:
            sent["X-API-Key"] = self.cfg.api_key
            shown["X-API-Key"] = self.cfg.api_key[:12] + "…"
        log.info("→ request", method=method, path=path, headers=shown, body=body)
        response = self.http.request(method, path, headers=sent, json=body)
        level = (
            log.error
            if response.status_code >= 500
            else (log.warning if response.status_code >= 400 else log.info)
        )
        level("← response", status=response.status_code, **self._summary(response))
        return response

    @staticmethod
    def _summary(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            return {"body": response.text[:120]}
        if isinstance(data, dict) and "error" in data:
            return {"error": data["error"]}
        if isinstance(data, dict) and "model" in data and "answer" in data:
            m = data["model"]
            return {
                "answer": data["answer"],
                "intent": data["intent"],
                "model": f"{m['requested']} → {m['used']} (source={m['source']})",
                "attempts": m["attempts"],
                "chain": m["chain"],
                "fallback_used": m["fallback_used"],
                "request_id": data["request_id"],
                "trace_id": data["trace_id"],
                "latency_ms": data["latency_ms"],
            }
        return {"body": json.dumps(data)[:300]}

    def _langfuse_project(self) -> str | None:
        if not self.cfg.langfuse_enabled:
            return None
        try:
            r = httpx.get(
                f"{self.cfg.langfuse_host}/api/public/projects",
                auth=(self.cfg.langfuse_public, self.cfg.langfuse_secret),
                timeout=10,
            )
            projects = r.json().get("data", [])
            return str(projects[0]["id"]) if projects else None
        except Exception:
            return None

    # --- the walkthrough ------------------------------------------------------------
    def run(self) -> None:
        log.info(
            "demo walkthrough",
            api=self.cfg.base_url,
            session=self.session,
            swagger=f"{self.cfg.base_url}/docs",
        )
        # Quiet prep so the fallback demo is deterministic: chain premium → standard → economy.
        self.http.put(
            "/models/fallback",
            headers={"X-API-Key": self.cfg.api_key},
            json={"model_id": "mock-standard"},
        )

        self.step("Is the service ready?")
        r = self.call("GET", "/health/ready", key=False)
        kafka_on = "kafka" in r.json().get("checks", {})
        self.why(
            "Readiness checks the database. Liveness (/health) checks only the process,",
            "so a database outage never restarts healthy containers.",
        )

        self.step("No API key → 401 (an alerting 4xx)")
        self.call("POST", "/ask", key=False, body={"question": "hi"}, request_id="demo-401")
        self.why(
            "Only registered users get in. The response carries a stable error code and the request id.",
            "401 and 403 are the only 4xx that raise an alert (Http4xxAuthErrors); others are just logged.",
            'auth_failures_total{reason="missing"} went up by one.',
        )
        self.grafana(21, "401 / 403 by route")

        self.step("The model catalog, by tier")
        r = self.call("GET", "/models")
        items = r.json().get("items", [])
        by_tier: dict[str, list[str]] = {}
        for m in items:
            by_tier.setdefault(m["tier"], []).append(
                m["id"] + ("" if m["available"] else " (no key)")
            )
        for tier in ("economy", "standard", "premium"):
            log.info("tier", tier=tier, models=by_tier.get(tier, []))
        self.why(
            "OpenAI, Anthropic, Gemini, bring-your-own and mock models, in three tiers.",
            "Pick one per request with X-Model-Id; each user can set a default and a fallback.",
        )

        self.step("Success: create a ticket")
        r = self.call(
            "POST",
            "/ask",
            body={"question": "Create a high priority ticket: checkout page is slow"},
            request_id="demo-ticket",
        )
        data = r.json()
        self.ticket_id = (data.get("action") or {}).get("ticket", {}).get("id")
        self.why(
            "The agent planned the action with an LLM, then the create_ticket tool wrote to Postgres.",
            "request_id, session_id and trace_id join this request's logs, spans and Langfuse trace.",
        )
        self.jaeger(data.get("trace_id"))
        self.langfuse()

        if self.ticket_id:
            self.step("Read the ticket back (UUID id)")
            r = self.call("GET", f"/tickets/{self.ticket_id}")
            self.why(
                "The ticket stores the trace_id that created it: support can jump to the trace."
            )
            self.jaeger(r.json().get("trace_id"))

        self.step("Choose the model with a header")
        r = self.call(
            "POST",
            "/ask",
            body=REPORT,
            headers={"X-Model-Id": "mock-premium"},
            request_id="demo-header",
        )
        self.why("model.source is `header`: X-Model-Id wins over the body and the user's default.")

        self.step("Invalid request → 422 (logged, no alert)")
        self.call("POST", "/ask", body={"question": ""}, request_id="demo-422")
        self.why(
            "Validation fails before any LLM call, so a bad request costs nothing.",
            'Counted in ask_errors_total{code="VALIDATION_ERROR"}, but no alert: it is the caller\'s error.',
        )
        self.prom(
            "validation errors", 'sum(increase(ask_errors_total{code="VALIDATION_ERROR"}[5m]))'
        )

        self.step("LLM outage across two models → fallback")
        r = self.call(
            "POST",
            "/ask",
            body=REPORT,
            headers={
                "X-Model-Id": "mock-premium",
                "X-Debug-Fault": "llm_timeout",
                "X-Debug-Fault-Count": "2",
            },
            request_id="demo-fallback",
        )
        self.why(
            "mock-premium timed out 3 times, then mock-standard 3 times, then mock-economy answered.",
            "7 attempts, each its own llm.call span. The user still got an answer.",
            "Alert LlmFallbacks fires for each hop.",
        )
        self.jaeger(r.json().get("trace_id"))
        self.grafana(30, "Retries and fallbacks")
        self.langfuse()

        self.step("Async job: 202 now, poll the in-flight status")
        r = self.call(
            "POST",
            "/ask",
            body=REPORT,
            headers={
                "Prefer": "respond-async",
                "X-Model-Id": "mock-premium",
                "X-Debug-Fault": "llm_timeout",
                "X-Debug-Fault-Count": "2",
            },
            request_id="demo-async",
        )
        accepted = r.json()
        self.why("Slow real LLMs should not hold the connection: the caller gets a job id at once.")
        self._poll_job(accepted.get("status_url"))
        self.jaeger(accepted.get("trace_id"))
        self.grafana(40, "Async job transitions")
        if kafka_on:
            self.link(
                "Kafka UI · ai-service.ask-status",
                f"{self.cfg.kafka_ui}/ui/clusters/local/all-topics/ai-service.ask-status/messages",
            )
        else:
            log.info(
                "kafka",
                note="KAFKA_ENABLED=false: status is in Postgres only (set it to stream to Kafka)",
            )

        self.step("A business action fails → 502 (alerting 5xx)")
        r = self.call(
            "POST",
            "/ask",
            body={"question": "Create a ticket: search is broken"},
            headers={"X-Debug-Fault": "tool_error"},
            request_id="demo-502",
        )
        self.why(
            "The tool failed after the LLM answered: 502 TOOL_FAILED, red tool span, ERROR log.",
            "Even the first 502 is visible in Grafana: every series is pre-created at 0.",
        )
        self.grafana(20, "5xx by route and status")
        self.link("Prometheus · alerts", f"{self.cfg.prometheus}/alerts")

        if self.wait_alerts:
            self.step("Alerts arrive as in-app notifications")
            self._wait_for_alerts()
            self.link("Alertmanager", f"{self.cfg.alertmanager}/#/alerts")
            self.grafana(60, "Firing alerts")

        print()
        log.info(
            "done",
            session=self.session,
            tip="Grafana → Observable AI Service for the whole picture",
        )

    def _poll_job(self, status_url: str | None) -> None:
        if not status_url:
            return
        seen = 0
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = self.http.get(status_url, headers={"X-API-Key": self.cfg.api_key}).json()
            for event in job.get("events", [])[seen:]:
                details = {k: v for k, v in event.items() if k not in ("status", "at")}
                fn = (
                    log.warning
                    if event["status"] in ("retrying", "fallback", "failed")
                    else log.info
                )
                fn("job status", status=event["status"], **details)
            seen = len(job.get("events", []))
            if job.get("status") in ("completed", "failed"):
                result = job.get("result") or {}
                log.info(
                    "job finished",
                    status=job["status"],
                    attempts=job["attempts"],
                    chain=(result.get("model") or {}).get("chain"),
                    answer=result.get("answer"),
                    error=job.get("error"),
                )
                return
            time.sleep(0.5)
        log.error("job did not finish in 60s", status_url=status_url)

    def _wait_for_alerts(self) -> None:
        wanted = {"Http5xxErrors", "Http4xxAuthErrors"}
        log.info("waiting for Alertmanager", alerts=sorted(wanted), up_to_seconds=self.wait_alerts)
        deadline = time.monotonic() + self.wait_alerts
        while time.monotonic() < deadline:
            data = self.http.get(
                "/notifications",
                params={"status": "firing"},
                headers={"X-API-Key": self.cfg.api_key},
            ).json()
            got = {i["alertname"] for i in data.get("items", [])}
            if wanted <= got:
                for item in data["items"]:
                    log.warning(
                        "notification",
                        alert=item["alertname"],
                        severity=item["severity"],
                        summary=item["summary"],
                        status=item["status"],
                    )
                self.why(
                    "Prometheus rule → Alertmanager → POST /alerts/webhook → Postgres → GET /notifications.",
                    "With KAFKA_ENABLED they are also published to ai-service.notifications.",
                )
                return
            time.sleep(3)
        log.error(
            "alerts did not arrive in time",
            hint="check http://localhost:9090/alerts and Alertmanager",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pause", action="store_true", help="wait for Enter between steps")
    parser.add_argument("--session", default=f"demo-{uuid.uuid4().hex[:6]}", help="X-Session-ID")
    parser.add_argument(
        "--base-url", default=None, help="API base URL (default http://localhost:8000)"
    )
    parser.add_argument(
        "--wait-alerts", type=int, default=60, help="seconds to wait for alerts (0 = skip)"
    )
    args = parser.parse_args()
    cfg = Config.load(args.base_url)
    cfg.require_api_key()
    Walkthrough(cfg, args.session, args.pause, args.wait_alerts).run()


if __name__ == "__main__":
    main()
