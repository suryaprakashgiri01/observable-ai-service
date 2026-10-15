"""Generate traffic that moves every metric on the Grafana dashboard.

Covers every intent, model selection via header and body, every 4xx and 5xx the
API produces, auth failures, single- and multi-model LLM timeouts, permanent LLM
errors, tool failures, an unreachable bring-your-own model, async jobs, and
default/fallback changes. Then it prints which metric families moved.

    uv run python -m scripts.generate_traffic                 # 3 rounds
    uv run python -m scripts.generate_traffic --rounds 10 --concurrency 8
    uv run python -m scripts.generate_traffic --outage        # + stop/start Postgres (500s)
    make traffic ARGS="--rounds 5"

Fault cases need ENABLE_FAULT_INJECTION=true in .env.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from scripts._common import Config, compose, console_logger, running_services

log = console_logger("traffic")

REPORT = {"question": "Give me a revenue report for the last 7 days"}


@dataclass(frozen=True)
class Call:
    name: str
    method: str
    path: str
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    key: str | None = "demo"  # "demo" | "analyst" | "bad" | None


SCENARIOS: list[Call] = [
    # successes: every intent, every model-selection source
    Call("ticket", "POST", "/ask", {"question": "Create a high priority ticket: checkout is slow"}),
    Call(
        "ticket-critical", "POST", "/ask", {"question": "Open a critical incident: payments down"}
    ),
    Call(
        "ticket-low", "POST", "/ask", {"question": "Create a low priority ticket: typo on footer"}
    ),
    Call("report-revenue", "POST", "/ask", REPORT),
    Call("report-orders", "POST", "/ask", {"question": "How many orders in the last 30 days?"}),
    Call("report-aov", "POST", "/ask", {"question": "Average order value this quarter?"}),
    Call("general", "POST", "/ask", {"question": "What can you do?"}),
    Call("model-body", "POST", "/ask", {**REPORT, "model_id": "mock-standard"}),
    Call("model-header", "POST", "/ask", REPORT, {"X-Model-Id": "mock-premium"}),
    Call("list-models", "GET", "/models"),
    Call("list-premium", "GET", "/models?tier=premium"),
    Call("notifications", "GET", "/notifications"),
    # 4xx: logged and counted (401/403 also alert)
    Call("401-missing-key", "POST", "/ask", {"question": "hi"}, key=None),
    Call("401-bad-key", "GET", "/models", key="bad"),
    Call("422-empty", "POST", "/ask", {"question": ""}),
    Call("422-bad-enum", "GET", "/models?tier=gold"),
    Call("400-unknown-model", "POST", "/ask", REPORT, {"X-Model-Id": "nope"}),
    Call("400-unavailable", "POST", "/ask", {**REPORT, "model_id": "claude-opus-5-5"}),
    Call("404-ticket", "GET", "/tickets/3f2a9c1e-6b7d-4e0a-9c51-2d8e7f1b4a60"),
    Call("404-route", "GET", "/nope", key=None),
    Call(
        "409-conflict",
        "POST",
        "/models/custom",
        {"id": "gpt-4.1", "name": "x", "base_url": "http://x.test/v1", "provider_model": "m"},
    ),
    # LLM faults: retries, fallbacks, multi-hop
    Call("fault-timeout", "POST", "/ask", REPORT, {"X-Debug-Fault": "llm_timeout"}),
    Call("fault-error", "POST", "/ask", REPORT, {"X-Debug-Fault": "llm_error"}),
    Call(
        "fault-multi",
        "POST",
        "/ask",
        REPORT,
        {"X-Model-Id": "mock-premium", "X-Debug-Fault": "llm_timeout", "X-Debug-Fault-Count": "2"},
    ),
    # 5xx
    Call(
        "502-tool",
        "POST",
        "/ask",
        {"question": "Create a ticket: search is broken"},
        {"X-Debug-Fault": "tool_error"},
    ),
    Call("502-tool-report", "POST", "/ask", REPORT, {"X-Debug-Fault": "tool_error"}),
]


class Traffic:
    def __init__(self, cfg: Config, concurrency: int) -> None:
        self.cfg = cfg
        self.sem = asyncio.Semaphore(concurrency)
        self.http = httpx.AsyncClient(base_url=cfg.base_url, timeout=90)
        self.statuses: Counter[tuple[str, int]] = Counter()

    def _headers(self, call: Call) -> dict[str, str]:
        keys = {
            "demo": self.cfg.api_key,
            "analyst": self.cfg.analyst_key or self.cfg.api_key,
            "bad": "oas_not_a_real_key",
        }
        headers = {"X-Session-ID": "traffic", **call.headers}
        if call.key:
            headers["X-API-Key"] = keys[call.key]
        return headers

    async def send(self, call: Call) -> httpx.Response | None:
        async with self.sem:
            try:
                r = await self.http.request(
                    call.method, call.path, json=call.body, headers=self._headers(call)
                )
            except httpx.HTTPError as exc:
                log.error("request failed", scenario=call.name, error=type(exc).__name__)
                self.statuses[(call.name, 0)] += 1
                return None
        self.statuses[(call.name, r.status_code)] += 1
        return r

    async def async_jobs(self, n: int) -> None:
        headers = {
            "Prefer": "respond-async",
            "X-Model-Id": "mock-premium",
            "X-Debug-Fault": "llm_timeout",
            "X-Debug-Fault-Count": "1",
        }
        urls = []
        for _ in range(n):
            r = await self.send(Call("async-job", "POST", "/ask", REPORT, headers))
            if r is not None and r.status_code == 202:
                urls.append(r.json()["status_url"])
        failing = await self.send(
            Call(
                "async-job-fail",
                "POST",
                "/ask",
                {"question": "Create a ticket: x"},
                {"Prefer": "respond-async", "X-Debug-Fault": "tool_error"},
            )
        )
        if failing is not None and failing.status_code == 202:
            urls.append(failing.json()["status_url"])
        for url in urls:
            for _ in range(120):
                job = (await self.http.get(url, headers={"X-API-Key": self.cfg.api_key})).json()
                if job.get("status") in ("completed", "failed"):
                    self.statuses[(f"job-{job['status']}", 200)] += 1
                    break
                await asyncio.sleep(0.5)

    async def byom_and_config(self) -> None:
        dead = {
            "id": "traffic-dead",
            "name": "Dead endpoint",
            "base_url": "http://byom.invalid/v1",
            "provider_model": "llama3",
            "tier": "economy",
        }
        await self.send(Call("byom-register", "POST", "/models/custom", dead))
        await self.send(
            Call("byom-unreachable", "POST", "/ask", REPORT, {"X-Model-Id": "traffic-dead"})
        )
        await self.send(Call("byom-delete", "DELETE", "/models/custom/traffic-dead"))
        await self.send(
            Call("set-default", "PUT", "/models/default", {"model_id": "mock-standard"})
        )
        await self.send(Call("default-used", "POST", "/ask", REPORT))
        await self.send(
            Call("set-default-back", "PUT", "/models/default", {"model_id": "mock-echo"})
        )
        await self.send(
            Call("set-fallback", "PUT", "/models/fallback", {"model_id": "mock-standard"})
        )

    async def outage(self) -> None:
        if "postgres" not in running_services():
            log.warning("outage skipped", reason="postgres is not running under compose")
            return
        log.warning("stopping postgres for ~10s (expect 500s and readiness 503)")
        compose("stop", "postgres")
        try:
            for _ in range(5):
                await self.send(Call("500-db-down", "GET", "/models"))
                await self.send(Call("503-ready", "GET", "/health/ready", key=None))
                await asyncio.sleep(1)
        finally:
            compose("start", "postgres")
            for _ in range(30):
                r = await self.send(Call("ready-again", "GET", "/health/ready", key=None))
                if r is not None and r.status_code == 200:
                    break
                await asyncio.sleep(1)
        log.info("postgres back")


async def metric_totals(cfg: Config) -> dict[str, float]:
    async with httpx.AsyncClient(timeout=10) as http:
        text = (await http.get(f"{cfg.base_url}/metrics")).text
    totals: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([-+0-9.eE]+|NaN)", line)
        # *_created is a per-series timestamp, not a value: skip it.
        if match and match.group(2) != "NaN" and not match.group(1).endswith("_created"):
            name = re.sub(r"_(bucket|count|sum)$", "", match.group(1))
            totals[name] = totals.get(name, 0.0) + float(match.group(2))
    return totals


async def run(args: argparse.Namespace) -> None:
    cfg = Config.load(args.base_url)
    cfg.require_api_key()
    traffic = Traffic(cfg, args.concurrency)
    before = await metric_totals(cfg)
    started = time.monotonic()
    log.info("generating traffic", rounds=args.rounds, scenarios=len(SCENARIOS), api=cfg.base_url)

    for round_no in range(1, args.rounds + 1):
        await asyncio.gather(*(traffic.send(c) for c in SCENARIOS))
        log.info("round done", round=round_no)
    await traffic.byom_and_config()
    await traffic.async_jobs(args.jobs)
    if args.outage:
        await traffic.outage()

    after = await metric_totals(cfg)
    await traffic.http.aclose()

    print()
    log.info("responses by scenario", seconds=round(time.monotonic() - started, 1))
    for (name, status), count in sorted(traffic.statuses.items()):
        fn = (
            log.error
            if status >= 500 or status == 0
            else (log.warning if status >= 400 else log.info)
        )
        fn("scenario", name=name, status=status, count=count)

    print()
    moved = sorted(n for n in after if after.get(n, 0) != before.get(n, 0))
    # Gauges that return to 0 when the run ends (in-flight counts) cannot be judged by a diff.
    gauges = ("http_requests_in_progress", "ask_jobs_in_flight", "notifications_unread")
    still = sorted(
        n
        for n in after
        if n not in moved and not n.startswith(("process_", "python_")) and n not in gauges
    )
    log.info("metric families that moved", count=len(moved), families=moved)
    if still:
        log.warning(
            "metric families unchanged",
            families=still,
            hint="faults need ENABLE_FAULT_INJECTION=true; kafka_* needs KAFKA_ENABLED=true",
        )
    log.info("open", grafana=f"{cfg.grafana}/d/observable-ai-service/observable-ai-service")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--jobs", type=int, default=3, help="async jobs to run")
    parser.add_argument("--outage", action="store_true", help="stop/start Postgres to produce 500s")
    parser.add_argument("--base-url", default=None)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
