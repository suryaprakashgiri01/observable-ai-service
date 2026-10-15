"""Every Prometheus metric the service exposes, defined in one place.

Labels are bounded on purpose: routes are templates, models come from the
catalog, codes from ``ErrorCode``. An unbounded label (a user id, a raw path)
creates a time series per value and eventually takes Prometheus down.
"""

from __future__ import annotations

from collections.abc import Iterable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

REGISTRY = CollectorRegistry(auto_describe=True)

# CPU, memory, open fds (Linux only: read from /proc), GC and Python version, for the
# dashboard's runtime row. The library defaults are bound to the global registry.
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

# Tuned for an API whose fast path is ~10 ms and whose LLM path is seconds.
_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60)
_LLM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60)

# --- HTTP ----------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template and status.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "route"],
    buckets=_HTTP_BUCKETS,
    registry=REGISTRY,
)
HTTP_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Requests currently being served.",
    registry=REGISTRY,
)

# --- /ask ----------------------------------------------------------------
ASK_REQUESTS = Counter(
    "ask_requests_total",
    "/ask outcomes by detected intent.",
    ["intent", "outcome"],
    registry=REGISTRY,
)
ASK_ERRORS = Counter(
    "ask_errors_total",
    "Error responses by error code (all routes).",
    ["code"],
    registry=REGISTRY,
)

# --- LLM -----------------------------------------------------------------
LLM_REQUESTS = Counter(
    "llm_requests_total",
    "LLM call attempts by provider, model, tier and outcome.",
    ["provider", "model", "tier", "outcome"],
    registry=REGISTRY,
)
LLM_LATENCY = Histogram(
    "llm_request_duration_seconds",
    "Latency of a single LLM call attempt.",
    ["provider", "model"],
    buckets=_LLM_BUCKETS,
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Tokens consumed, by type (input/output).",
    ["provider", "model", "type"],
    registry=REGISTRY,
)
LLM_RETRIES = Counter(
    "llm_retries_total",
    "Retries after a transient LLM failure.",
    ["model"],
    registry=REGISTRY,
)
LLM_FALLBACKS = Counter(
    "llm_fallbacks_total",
    "Switches from one model to the next in the fallback chain.",
    ["from_model", "to_model", "reason"],
    registry=REGISTRY,
)
MODEL_SELECTION = Counter(
    "model_selection_total",
    "Where the model for a request came from (header, body, defaults).",
    ["source"],
    registry=REGISTRY,
)

# --- business actions ---------------------------------------------------------
TOOL_CALLS = Counter(
    "agent_tool_calls_total",
    "Agent tool executions by tool and outcome.",
    ["tool", "outcome"],
    registry=REGISTRY,
)
TICKETS_CREATED = Counter(
    "tickets_created_total",
    "Tickets created through the agent.",
    ["priority"],
    registry=REGISTRY,
)

# --- auth ----------------------------------------------------------------
AUTH_FAILURES = Counter(
    "auth_failures_total",
    "Rejected requests by reason.",
    ["reason"],
    registry=REGISTRY,
)

# --- alerts and notifications -------------------------------------------------------
ALERTS_RECEIVED = Counter(
    "alerts_received_total",
    "Alertmanager notifications received by the webhook.",
    ["alertname", "status"],
    registry=REGISTRY,
)
NOTIFICATIONS_UNREAD = Gauge(
    "notifications_unread",
    "In-app notifications not yet marked read.",
    registry=REGISTRY,
)

# --- async jobs and Kafka -------------------------------------------------------------
ASK_JOBS = Counter(
    "ask_jobs_total",
    "Async /ask job status transitions.",
    ["status"],
    registry=REGISTRY,
)
ASK_JOBS_IN_FLIGHT = Gauge(
    "ask_jobs_in_flight",
    "Async /ask jobs currently running in this process.",
    registry=REGISTRY,
)
KAFKA_PUBLISH = Counter(
    "kafka_publish_total",
    "Events published to Kafka by topic and outcome (only when KAFKA_ENABLED).",
    ["topic", "outcome"],
    registry=REGISTRY,
)

# Bounded label values known up front. Pre-creating them at 0 matters: a series
# born at 1 has no earlier sample, so rate()/increase() never show its first event.
HTTP_STATUSES_TO_INIT = ("400", "401", "403", "404", "409", "422", "500", "502", "503")
AUTH_FAILURE_REASONS = ("missing", "unknown", "revoked", "inactive_user", "forbidden")
TOOLS = ("create_ticket", "generate_report")
TICKET_PRIORITIES = ("low", "medium", "high", "critical")
SELECTION_SOURCES = ("header", "body", "user_default", "global_default", "mock")
JOB_STATUSES = ("queued", "running", "retrying", "fallback", "completed", "failed")
ALERT_STATUSES = ("firing", "resolved")
KAFKA_OUTCOMES = ("success", "error")


def init_metric_series(
    *,
    routes: Iterable[tuple[str, str]],
    error_codes: Iterable[str],
    kafka_topics: Iterable[str],
) -> None:
    """Create every bounded series at 0. Idempotent."""
    for method, route in routes:
        for status in HTTP_STATUSES_TO_INIT:
            HTTP_REQUESTS.labels(method=method, route=route, status=status)
    for code in error_codes:
        ASK_ERRORS.labels(code=code)
    for reason in AUTH_FAILURE_REASONS:
        AUTH_FAILURES.labels(reason=reason)
    for tool in TOOLS:
        for outcome in ("success", "error"):
            TOOL_CALLS.labels(tool=tool, outcome=outcome)
    for priority in TICKET_PRIORITIES:
        TICKETS_CREATED.labels(priority=priority)
    for source in SELECTION_SOURCES:
        MODEL_SELECTION.labels(source=source)
    for status in JOB_STATUSES:
        ASK_JOBS.labels(status=status)
    for topic in kafka_topics:
        for outcome in KAFKA_OUTCOMES:
            KAFKA_PUBLISH.labels(topic=topic, outcome=outcome)
