"""Shared helpers for the ops scripts: config from .env, compose detection, coloured logs.

Scripts run on macOS and Windows (inside or outside WSL) with ``uv run python -m scripts.<name>``.
Secrets are read from .env at runtime and never printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

ROOT = Path(__file__).resolve().parents[1]


def read_env() -> dict[str, str]:
    """`.env` values, overridden by the real environment."""
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            # Strip an inline comment ("value   # note"), then quotes.
            value = raw.split(" #", 1)[0].strip().strip('"').strip("'")
            values[key.strip()] = value
    for key, value in os.environ.items():
        if key.isupper():
            values[key] = value
    return values


@dataclass(frozen=True)
class Config:
    base_url: str
    api_key: str
    analyst_key: str
    grafana: str
    jaeger: str
    prometheus: str
    alertmanager: str
    kafka_ui: str
    langfuse_host: str
    langfuse_public: str
    langfuse_secret: str
    database_url: str

    @classmethod
    def load(cls, base_url: str | None = None) -> Config:
        env = read_env()
        return cls(
            base_url=(base_url or env.get("APP_URL") or "http://localhost:8000").rstrip("/"),
            api_key=env.get("SEED_DEMO_API_KEY", ""),
            analyst_key=env.get("SEED_ANALYST_API_KEY", ""),
            grafana=env.get("GRAFANA_URL", "http://localhost:3000"),
            jaeger=env.get("JAEGER_URL", "http://localhost:16686"),
            prometheus=env.get("PROMETHEUS_URL", "http://localhost:9090"),
            alertmanager=env.get("ALERTMANAGER_URL", "http://localhost:9093"),
            kafka_ui=env.get("KAFKA_UI_URL", "http://localhost:8080"),
            langfuse_host=(
                env.get("LANGFUSE_BASE_URL")
                or env.get("LANGFUSE_HOST")
                or "https://cloud.langfuse.com"
            ).rstrip("/"),
            langfuse_public=env.get("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret=env.get("LANGFUSE_SECRET_KEY", ""),
            database_url=env.get(
                "DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5433/ai_service"
            ),
        )

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public and self.langfuse_secret)

    def require_api_key(self) -> None:
        if not self.api_key:
            sys.exit("SEED_DEMO_API_KEY is not set in .env: run `make seed` with a pinned key.")


def compose_cmd() -> list[str] | None:
    """Same order as the Makefile: docker compose, docker-compose, podman compose."""
    candidates = [["docker", "compose"], ["docker-compose"], ["podman", "compose"]]
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        probe = subprocess.run(
            [*cmd, "version"], capture_output=True, text=True, cwd=ROOT, check=False
        )
        if probe.returncode == 0:
            return cmd
    return None


def compose(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = compose_cmd()
    if cmd is None:
        sys.exit("No compose command found (docker compose / docker-compose / podman compose).")
    env = {**os.environ, "PODMAN_COMPOSE_WARNING_LOGS": "false"}
    return subprocess.run(
        [*cmd, *args], capture_output=True, text=True, cwd=ROOT, check=check, env=env
    )


def running_services() -> set[str]:
    result = compose("ps", "--services", "--status", "running")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def console_logger(name: str) -> Any:
    """Coloured, human-readable structlog output for the scripts (not the app's JSON)."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty() or _force_color()),
        ],
        cache_logger_on_first_use=False,
    )
    return structlog.get_logger(name)


def _force_color() -> bool:
    return os.environ.get("FORCE_COLOR", "") not in ("", "0")
