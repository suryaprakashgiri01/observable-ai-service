# Architecture

## Request path

```
POST /ask
 ├─ RequestContextMiddleware     X-Request-ID → contextvars → every log line; echoed on the response
 ├─ MetricsMiddleware            http_requests_total / duration / in-progress, by route template
 ├─ OTel FastAPI instrumentation server span; honours an incoming traceparent
 ├─ get_current_user             X-API-Key → sha256 → api_keys ⋈ users (Postgres); binds user_id
 └─ ask()
     ├─ span model.resolve       ModelRegistry: header → body → user default → global default
     │                           (+ fallback: user's, else global; never the same model)
     ├─ span agent.plan          LLMOrchestrator.complete()
     │    └─ span llm.call × N   tenacity retry on TransientLLMError (timeout/429/5xx)
     │                           then next model in chain: selected → fallback → mock-echo
     └─ span tool.<name>         create_ticket → Postgres  |  generate_report → mock_orders.json
```

## Alerts and async jobs

```
Prometheus rules ──▶ Alertmanager ──▶ POST /alerts/webhook ──▶ notifications (Postgres)
                                                  └─ KAFKA_ENABLED ─▶ ai-service.notifications
POST /ask (Prefer: respond-async) ──▶ ask_jobs (queued) ──▶ 202 {job_id}
    └─ background task, span ask.job (same trace, session, Langfuse trace)
         └─ orchestrator status hook: running · retrying · fallback · completed · failed
              ├─ ask_jobs.events (GET /ask/jobs/{id})
              └─ KAFKA_ENABLED ─▶ ai-service.ask-status
```

Postgres is the source of truth for both. Kafka is an optional copy, so the API never depends
on the broker, and a publish failure is logged and counted, never raised.

## Modules

| Path | Responsibility |
|---|---|
| `app/core/` | settings, structlog JSON logging, contextvars, Prometheus registry, OTel setup, Fernet |
| `app/middleware/` | request id + access log, HTTP RED metrics, route-template resolution |
| `app/exceptions/` | stable `ErrorCode` → HTTP status, one error envelope; a hook counts every error |
| `app/auth/` | key generation/hashing, `CurrentUser` dependency |
| `app/db/`, `migrations/` | async SQLAlchemy, ORM models, Alembic |
| `app/llm/` | provider-neutral types, catalog, DB-backed registry, orchestrator, Langfuse recorder |
| `app/llm/providers/` | OpenAI (+ BYOM), Anthropic, Gemini, mock — all raw `httpx`, one shared client |
| `app/services/` | agent (JSON plan → tool), tools |
| `app/integrations/events.py` | `EventPublisher`: no-op, in-memory (tests) or Kafka (flag) |
| `app/services/jobs.py`, `app/llm/status.py` | async job runner and the status hook |
| `app/api/routes/alerts.py` | Alertmanager webhook, notifications |
| `scripts/` | seeds, traffic generator, cleanup, demo walkthrough |
| `deploy/grafana/build_dashboard.py` | the dashboard as code (fixed panel ids for alert links) |
| `deploy/prometheus-alerts.yml`, `deploy/alertmanager.yml` | alert rules and routing |
| `deploy/` | Prometheus scrape config, Grafana datasources + dashboard |

## Why these choices

- **Own orchestrator over raw httpx, not vendor SDKs.** One HTTP client gives one timeout policy
  and one retry policy. Error classification (`TransientLLMError` vs `PermanentLLMError`) lives in
  one function, and httpx auto-instrumentation gives an HTTP span for every provider call
  regardless of vendor.
- **JSON planning instead of native tool calling.** Tool-calling formats differ per vendor and
  many BYOM servers lack them. A validated JSON plan works on all of them, including the mock.
- **Templated tool answers.** After a tool runs, the answer is formatted from its result, with no
  second LLM call. That halves latency and cost, and the numbers in the answer are exactly what
  the tool computed.
- **Mock as a real model.** `mock-echo` sits in the catalog, is always available, and is the last
  link of every chain. It is also what lets the whole pipeline run in CI and in a demo without keys.
- **Fault injection by header, gated by a setting.** Failure modes can be reproduced on demand
  without editing code. Off unless `ENABLE_FAULT_INJECTION=true`.

## Data

`users`, `api_keys` (sha256 only), `models` (global catalog rows have `owner_user_id IS NULL`;
BYOM rows belong to a user and hold a Fernet-encrypted key), `user_model_config`
(default/fallback per user), `tickets` (with the `request_id` and `trace_id` that created them).

## Label cardinality

Metric labels are bounded: route *templates* (`/tickets/{ticket_id}`), model ids from the catalog
plus at most 10 custom models per user, error codes from an enum. No user ids, raw paths or
questions go into labels; those belong in logs and span attributes.
