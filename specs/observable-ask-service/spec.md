# Spec: observable-ask-service

Status: approved · Owner: Surya Prakash Giri · Ticket: none (personal repo; `tasks.md` is the task list)

## Problem
Engineers running an AI-backed API need to answer "what happened to request X?", "why is p95 up?",
and "which model actually served this?" without attaching a debugger to production. We want a
small service whose every request is traceable end to end, and whose LLM dependency is
observable, swappable per request, and survivable when a provider fails.

## Goals
1. `POST /ask` answers a natural-language question through an LLM-driven agent.
2. The agent can run business actions: **create a ticket** and **generate a report** from mock data.
3. Every request is observable through structured logs, metrics and distributed traces, all
   joined by one `request_id` and one `trace_id`.
4. Callers pick a model per request; the service falls back to a cheaper model, then to a mock,
   when the chosen one fails.
5. Only registered users can call the API.

## Non-goals
UI; multi-tenant isolation; streaming responses; rate limiting; production secret management.

## Users & auth
- Users are registered by a seed script (`admin`, `demo`, `analyst`). No signup route.
- Every request except `/health`, `/health/ready`, `/metrics`, `/docs`, `/openapi.json` must send
  `X-API-Key`. Keys are stored as SHA-256 hashes; the plaintext is shown once at seed time.
- Missing header → `401 AUTH_REQUIRED`. Unknown, revoked, or inactive-user key → `401 AUTH_INVALID`.
- Admin-only: none in the request path; reserved for catalog management.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/ask` | Ask a question; may trigger a business action |
| GET | `/tickets/{id}` | Read back a ticket the caller created |
| GET | `/models` | Model catalog with tier, provider, availability, caller's default/fallback flags |
| PUT | `/models/default` | Set the caller's default model |
| PUT | `/models/fallback` | Set the caller's fallback model |
| POST | `/models/custom` | Register a bring-your-own (OpenAI-compatible) model |
| DELETE | `/models/custom/{id}` | Remove the caller's custom model |
| GET | `/health` | Liveness: process is up |
| GET | `/health/ready` | Readiness: database reachable, at least one model available |
| GET | `/metrics` | Prometheus text exposition |

### `POST /ask`
Request: `{"question": str 1..2000 chars (trimmed), "model_id": str | null}`
Headers: `X-API-Key` (required), `X-Model-Id` (optional, wins over body), `X-Request-ID`
(optional, echoed; generated when absent), `X-Session-ID` (optional; groups requests into one
session, defaults to the request id, echoed), `traceparent` (optional, W3C), `X-Debug-Fault`
(optional, only honoured when `ENABLE_FAULT_INJECTION=true`: `llm_timeout` | `llm_error` | `tool_error`).

Response 200:
```json
{
  "request_id": "…", "session_id": "…", "trace_id": "…32 hex…",
  "answer": "…", "intent": "create_ticket | generate_report | general",
  "action": {"type": "ticket_created", "ticket": {…}} | {"type": "report", "report": {…}} | null,
  "model": {"requested": "…", "used": "…", "source": "header|body|user_default|global_default",
            "fallback_used": false, "attempts": 1},
  "latency_ms": 123.4
}
```

### Model selection
Resolution order: `X-Model-Id` header → body `model_id` → caller's default → global default
(`LLM_DEFAULT_MODEL_ID`). Unknown id → `400 MODEL_NOT_FOUND`. Known but the provider has no
credentials → `400 MODEL_UNAVAILABLE`.

Execution: the selected model is retried on transient errors (timeout, 429, 5xx) up to
`LLM_MAX_ATTEMPTS` with exponential backoff. Exhausted → caller's fallback model, else the global
fallback (`LLM_FALLBACK_MODEL_ID`), one attempt series → `mock-echo` as the last resort, so `/ask`
degrades instead of failing. Non-transient errors (400/401/403 from a provider) skip retries.

### Model catalog
Each model: `id`, `name`, `provider` (`openai|anthropic|google|byom|mock`), `tier`
(`economy|standard|premium`), `available`, `is_default`, `is_fallback`, `owned` (custom model).
Seeded with three models per vendor (one per tier) plus `mock-echo`. Custom models are visible only
to their owner; their API key is encrypted at rest and never returned or logged.

### Error envelope
Every non-2xx: `{"error": {"code": "…", "message": "…", "request_id": "…", "details": …}}` and
the `X-Request-ID` response header.

| Code | Status |
|---|---|
| VALIDATION_ERROR | 422 |
| AUTH_REQUIRED / AUTH_INVALID | 401 |
| MODEL_NOT_FOUND / MODEL_UNAVAILABLE / MODEL_CONFLICT | 400 / 400 / 409 |
| TICKET_NOT_FOUND | 404 |
| TOOL_FAILED | 502 |
| INTERNAL_ERROR | 500 |

## Observability requirements
- **Logs**: one JSON object per line on stdout; every line in a request carries `request_id`,
  `trace_id`, `span_id`, `user_id` (once authenticated). One access line per request with
  method, route, status, `duration_ms`. Secrets (API keys) never appear.
- **Metrics** (`/metrics`, Prometheus):
  `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}` histogram,
  `http_requests_in_progress`, `ask_requests_total{intent,outcome}`, `ask_errors_total{code}`,
  `llm_requests_total{provider,model,tier,outcome}`, `llm_request_duration_seconds{provider,model}`,
  `llm_tokens_total{provider,model,type}`, `llm_retries_total{model}`,
  `llm_fallbacks_total{from_model,to_model,reason}`, `model_selection_total{source}`,
  `agent_tool_calls_total{tool,outcome}`, `tickets_created_total{priority}`, `auth_failures_total{reason}`.
  Route labels use the route template, never the raw path (cardinality).
- **Traces** (OpenTelemetry → OTLP/HTTP → Jaeger): server span per request (FastAPI
  instrumentation), child spans `model.resolve`, `agent.plan`, `llm.call` (one per attempt, with
  provider/model/tier/attempt/tokens/outcome), `tool.<name>`, outbound HTTP spans (httpx), and DB
  spans (SQLAlchemy). Incoming `traceparent` is honoured. `/health*` and `/metrics` are not traced.
- **LLM traces**: when Langfuse keys are configured, each `/ask` is a Langfuse trace named `ask`
  with `user_id` and `session_id` set, the question as input and the answer as output, tags for
  intent, model, `fallback` and any fault. Every LLM attempt is a generation nested under it
  (`llm.call <model> #<attempt>`) with model, input, output and token usage.
- **Sessions**: `X-Session-ID` (default: the request id) is on every log line (`session_id`), on
  the server span (`session.id`, searchable in Jaeger), in the response, and is the Langfuse
  session. Never a metric label.

## Acceptance criteria
1. `POST /ask` with a valid key and "Create a high priority ticket: checkout is slow" → 200,
   `intent=create_ticket`, a ticket row exists, `GET /tickets/{id}` returns it. *(API test)*
2. "Revenue report for the last 7 days" → 200, `intent=generate_report`, report totals computed
   from mock orders. *(API test)*
3. `{"question": ""}` → 422 `VALIDATION_ERROR` envelope carrying the `request_id`;
   `ask_errors_total{code="VALIDATION_ERROR"}` increments. *(API test)*
4. No key → 401 `AUTH_REQUIRED`; bad key → 401 `AUTH_INVALID`; `auth_failures_total` increments. *(API test)*
5. `X-Model-Id` overrides body `model_id`, which overrides the caller's default; response
   `model.source` says which. Unknown id → 400 `MODEL_NOT_FOUND`. *(unit + API tests)*
6. A provider that times out on every attempt → `LLM_MAX_ATTEMPTS` `llm.call` spans with
   `outcome=timeout`, then the fallback model answers; `llm_retries_total` and
   `llm_fallbacks_total` increment; response `fallback_used=true`. *(unit test with fake provider)*
7. `PUT /models/default` persists per user; a second user's default is unaffected. *(API test)*
8. `POST /models/custom` stores the key encrypted; `GET /models` never returns it. *(API test)*
9. `X-Request-ID` sent by the caller is echoed and appears on every log line of that request. *(test)*
9a. `X-Session-ID` groups requests: echoed, in the response, on the server span; defaults to the
   request id, so re-using an earlier `X-Request-ID` joins that session. *(test)*
10. `GET /metrics` exposes every metric family listed above. *(API test)*
11. `/health` 200 without auth; `/health/ready` 503 when the DB is unreachable. *(API test)*
12. With the compose stack up, Jaeger shows the span tree for one `/ask`. *(manual, demo script)*

## Open questions
None blocking. Model id strings are checked against provider docs at build time.
