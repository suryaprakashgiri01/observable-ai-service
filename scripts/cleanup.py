"""Reset what the demo and tests leave behind: DB rows, metrics, traces, alerts, Kafka, Langfuse.

    uv run python -m scripts.cleanup                    # asks before each part
    uv run python -m scripts.cleanup --yes              # everything, no questions
    uv run python -m scripts.cleanup --only db,langfuse
    uv run python -m scripts.cleanup --yes --reset-users   # also users/keys, then reseed
    uv run python -m scripts.cleanup --dry-run          # only count what would go
    make cleanup ARGS="--yes"

Parts:
  db        tickets, notifications, async jobs, per-user model config, custom models
  metrics   this service's Prometheus series (admin API) + restart the app (in-process counters)
  traces    restart Jaeger (in-memory store)
  alerts    restart Alertmanager (active alerts, silences)
  kafka     delete the two topics (recreated on the next publish), if the broker is running
  langfuse  delete this app's traces (environment=local), which also empties their sessions
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts._common import ROOT, Config, compose, console_logger, read_env, running_services

log = console_logger("cleanup")

PARTS = ("db", "metrics", "traces", "alerts", "kafka", "langfuse")
JOB = '{job="observable-ai-service"}'


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")


async def clean_db(cfg: Config, reset_users: bool) -> None:
    engine = create_async_engine(cfg.database_url)
    try:
        async with engine.begin() as conn:
            for table in ("tickets", "notifications", "ask_jobs", "user_model_config"):
                await conn.execute(text(f"DELETE FROM {table}"))
            custom = await conn.execute(text("DELETE FROM models WHERE owner_user_id IS NOT NULL"))
            if reset_users:
                await conn.execute(text("DELETE FROM api_keys"))
                await conn.execute(text("DELETE FROM users"))
        log.info("db cleaned", custom_models_removed=custom.rowcount, users_reset=reset_users)
    finally:
        await engine.dispose()


async def count_db(cfg: Config) -> dict[str, int]:
    engine = create_async_engine(cfg.database_url)
    counts: dict[str, int] = {}
    try:
        async with engine.connect() as conn:
            for table in ("tickets", "notifications", "ask_jobs", "user_model_config"):
                counts[table] = int(
                    (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                )
            counts["custom models"] = int(
                (
                    await conn.execute(
                        text("SELECT count(*) FROM models WHERE owner_user_id IS NOT NULL")
                    )
                ).scalar_one()
            )
    finally:
        await engine.dispose()
    return counts


def dry_run(cfg: Config, parts: list[str], environment: str) -> None:
    """Count what each part would remove. Deletes nothing."""
    if "db" in parts:
        try:
            log.info("db would delete", **asyncio.run(count_db(cfg)))
        except Exception as exc:
            log.error("db not reachable", error=type(exc).__name__)
    if "metrics" in parts:
        try:
            r = httpx.get(f"{cfg.prometheus}/api/v1/series", params={"match[]": JOB}, timeout=10)
            log.info("prometheus would delete", series=len(r.json().get("data", [])), match=JOB)
        except httpx.HTTPError as exc:
            log.error("prometheus not reachable", error=type(exc).__name__)
    services = running_services()
    for part, service in (("traces", "jaeger"), ("alerts", "alertmanager"), ("kafka", "kafka")):
        if part in parts:
            log.info(f"{part}: would restart/clear {service}", running=service in services)
    if "langfuse" in parts:
        if not cfg.langfuse_enabled:
            log.info("langfuse keys not set", skipped="langfuse")
        else:
            try:
                ids = langfuse_trace_ids(cfg, environment)
                log.info("langfuse would delete", traces=len(ids), environment=environment)
            except httpx.HTTPError as exc:
                log.error("langfuse list failed", error=str(exc)[:200])
    log.info("dry run: nothing was deleted")


def reseed() -> None:
    for module in ("scripts.seed_models", "scripts.seed_users"):
        subprocess.run([sys.executable, "-m", module], cwd=ROOT, check=False)


def clean_metrics(cfg: Config) -> None:
    base = cfg.prometheus
    try:
        for match in (JOB, '{__name__=~"ALERTS|ALERTS_FOR_STATE"}'):
            r = httpx.post(
                f"{base}/api/v1/admin/tsdb/delete_series", params={"match[]": match}, timeout=30
            )
            if r.status_code == 404 or r.status_code == 405:
                log.error(
                    "prometheus admin API is off",
                    fix="compose has --web.enable-admin-api; `make up`",
                )
                return
            r.raise_for_status()
        httpx.post(f"{base}/api/v1/admin/tsdb/clean_tombstones", timeout=60).raise_for_status()
        log.info("prometheus series deleted", match=JOB)
    except httpx.HTTPError as exc:
        log.error("prometheus not reachable", url=base, error=type(exc).__name__)
        return
    if "app" in running_services():
        compose("restart", "app")
        log.info("app restarted", why="resets in-process counters to 0")
    else:
        log.warning(
            "app not under compose", action="restart `make dev` to reset in-process counters"
        )


def restart(service: str, what: str) -> None:
    if service in running_services():
        compose("restart", service)
        log.info(f"{service} restarted", cleared=what)
    else:
        log.warning(f"{service} not running", skipped=what)


def clean_kafka() -> None:
    if "kafka" not in running_services():
        log.info("kafka not running", skipped="topics")
        return
    env = read_env()
    for topic in (
        env.get("KAFKA_TOPIC_NOTIFICATIONS", "ai-service.notifications"),
        env.get("KAFKA_TOPIC_ASK_STATUS", "ai-service.ask-status"),
    ):
        result = compose(
            "exec",
            "-T",
            "kafka",
            "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server",
            "localhost:9092",
            "--delete",
            "--if-exists",
            "--topic",
            topic,
        )
        if result.returncode == 0:
            log.info("kafka topic deleted", topic=topic, note="recreated on the next publish")
        else:
            log.error("kafka topic delete failed", topic=topic, stderr=result.stderr.strip()[-200:])


def langfuse_trace_ids(cfg: Config, environment: str) -> set[str]:
    """Root observations of this environment, paged by cursor (public API v2)."""
    ids: set[str] = set()
    params: dict[str, Any] = {
        "environment": environment,
        "isRootObservation": "true",
        "fields": "core",
        "limit": 1000,
        "fromStartTime": (datetime.now(UTC) - timedelta(days=90)).isoformat(),
    }
    with httpx.Client(auth=(cfg.langfuse_public, cfg.langfuse_secret), timeout=30) as http:
        while True:
            r = http.get(f"{cfg.langfuse_host}/api/public/v2/observations", params=params)
            r.raise_for_status()
            body = r.json()
            ids.update(o["traceId"] for o in body.get("data", []) if o.get("traceId"))
            cursor = (body.get("meta") or {}).get("cursor")
            if not cursor:
                return ids
            params["cursor"] = cursor


def clean_langfuse(cfg: Config, environment: str) -> None:
    if not cfg.langfuse_enabled:
        log.info("langfuse keys not set", skipped="langfuse")
        return
    try:
        ids = sorted(langfuse_trace_ids(cfg, environment))
    except httpx.HTTPError as exc:
        log.error("langfuse list failed", error=str(exc)[:200])
        return
    if not ids:
        log.info("langfuse: nothing to delete", environment=environment)
        return
    with httpx.Client(auth=(cfg.langfuse_public, cfg.langfuse_secret), timeout=60) as http:
        for start in range(0, len(ids), 500):
            batch = ids[start : start + 500]
            r = http.request(
                "DELETE", f"{cfg.langfuse_host}/api/public/traces", json={"traceIds": batch}
            )
            if r.status_code >= 400:
                log.error("langfuse delete failed", status=r.status_code, body=r.text[:200])
                return
    log.info(
        "langfuse traces deleted",
        environment=environment,
        traces=len(ids),
        note="deletion is asynchronous: the UI may take a minute; sessions empty with their traces",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--yes", "-y", action="store_true", help="do not ask")
    parser.add_argument("--dry-run", action="store_true", help="only count what would be removed")
    parser.add_argument("--only", default=",".join(PARTS), help=f"comma list of {', '.join(PARTS)}")
    parser.add_argument(
        "--reset-users", action="store_true", help="also delete users/keys, then reseed"
    )
    parser.add_argument(
        "--langfuse-env", default=None, help="Langfuse environment (default APP_ENV or local)"
    )
    args = parser.parse_args()

    cfg = Config.load()
    parts = [p.strip() for p in args.only.split(",") if p.strip()]
    unknown = set(parts) - set(PARTS)
    if unknown:
        sys.exit(f"unknown parts: {', '.join(sorted(unknown))}")
    environment = args.langfuse_env or read_env().get("APP_ENV", "local")

    log.info("cleanup", parts=parts, langfuse_environment=environment, dry_run=args.dry_run)
    if args.dry_run:
        dry_run(cfg, parts, environment)
        return
    if "db" in parts and confirm(
        "Delete tickets, notifications, jobs, user configs, custom models?", args.yes
    ):
        asyncio.run(clean_db(cfg, args.reset_users))
        if args.reset_users:
            reseed()
    if "metrics" in parts and confirm(
        "Delete this service's Prometheus series and restart the app?", args.yes
    ):
        clean_metrics(cfg)
    if "traces" in parts and confirm("Restart Jaeger (drops all traces)?", args.yes):
        restart("jaeger", "all traces")
    if "alerts" in parts and confirm(
        "Restart Alertmanager (drops active alerts and silences)?", args.yes
    ):
        restart("alertmanager", "active alerts, silences")
    if "kafka" in parts and confirm("Delete the Kafka topics?", args.yes):
        clean_kafka()
    if "langfuse" in parts and confirm(
        f"Delete Langfuse traces with environment={environment}?", args.yes
    ):
        clean_langfuse(cfg, environment)
    log.info("cleanup done")


if __name__ == "__main__":
    main()
