"""Every Prometheus metric the service exposes, defined in one place.

Labels are bounded on purpose: routes are templates, models come from the
catalog, codes from ``ErrorCode``. An unbounded label (a user id, a raw path)
creates a time series per value and eventually takes Prometheus down.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

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
