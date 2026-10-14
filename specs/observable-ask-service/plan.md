# Plan: observable-ask-service

Implements [spec.md](spec.md).

## Architecture

```
client ──HTTP──▶ FastAPI
                 ├─ RequestContextMiddleware   X-Request-ID, structlog contextvars, response header
                 ├─ MetricsMiddleware          http_* metrics by route template
                 ├─ OTel FastAPI instrumentation (server span, traceparent extraction)
                 ├─ auth dependency            X-API-Key → users/api_keys (Postgres)
                 └─ /ask ─▶ AgentService
                             ├─ ModelRegistry.resolve()      span model.resolve
                             ├─ LLMOrchestrator.complete()   span agent.plan
                             │    └─ retry loop per model    span llm.call × N  → httpx span → provider
                             │       fallback chain: selected → fallback → mock-echo
                             └─ tool dispatch                span tool.create_ticket | tool.generate_report
Postgres ◀── SQLAlchemy async (spans via OTel SQLAlchemy instrumentation)
Prometheus ◀─scrape─ /metrics          Jaeger ◀─OTLP/HTTP─ BatchSpanProcessor
Grafana ─▶ Prometheus (provisioned dashboard)      Langfuse ◀─ LLM generations (optional)
```

## Module layout
| Module | Responsibility |
|---|---|
| `app/core/config.py` | `Settings` (pydantic-settings), one cached instance |
| `app/core/logging.py` | structlog JSON, stdlib bridged, trace ids stamped on each line |
| `app/core/context.py` | request-scoped contextvars (request_id, user_id) |
| `app/core/metrics.py` | every Prometheus metric, defined once |
| `app/core/telemetry.py` | tracer provider, OTLP exporter, instrumentors, `tracer` |
| `app/middleware/` | request context + access log, HTTP metrics |
| `app/exceptions/` | `AppError(code, status)`, error codes, envelope handlers |
| `app/db/` | engine/session factory, ORM models |
| `migrations/` | Alembic, async env |
| `app/auth/` | key hashing, `CurrentUser` dependency |
| `app/llm/types.py` | `ChatMessage`, `LLMResult`, `ModelSpec`, error taxonomy (transient vs permanent) |
| `app/llm/providers/` | `LLMProvider` protocol; openai, openai_compatible (BYOM), anthropic, gemini, mock — all raw httpx |
| `app/llm/catalog.py` | seed catalog (3 per vendor + mock) |
| `app/llm/registry.py` | DB-backed model lookup, per-user default/fallback, availability |
| `app/llm/orchestrator.py` | retry + fallback chain, `llm.call` spans, metrics, Langfuse |
| `app/services/tools/` | `create_ticket`, `generate_report` (mock orders JSON) |
| `app/services/agent.py` | JSON-plan prompt, parse/repair, tool dispatch |
| `app/api/routes/` | ask, tickets, models, health, metrics |
| `scripts/` | `seed_models.py`, `seed_users.py` |

## Key decisions
- **Raw httpx instead of vendor SDKs.** One client gives one timeout policy, one retry policy, and
  httpx auto-instrumentation gives an HTTP span for every provider call. Cost: each adapter maps
  its own request/response shape (~50 lines each).
- **Provider-agnostic JSON planning, not native tool calling.** Tool-calling formats differ per
  vendor and BYOM endpoints often lack them. The agent asks for a JSON object and validates it
  with pydantic; one repair attempt, then degrade to a plain answer.
- **Mock provider is a first-class model** (`mock-echo`), rule-based intent detection. It makes the
  service runnable without keys and is the last link of the fallback chain.
- **Fault injection via header**, gated by a setting, so failure modes are reproducible on demand.
- **Retries with tenacity** on a typed `TransientLLMError`; every attempt is its own span so a
  slow request reads as "3 × 20 s timeouts" in Jaeger rather than one opaque 60 s span.
- **Prometheus pull + OTLP push.** Metrics are scraped (simple, dashboard-ready); traces are pushed
  with a `BatchSpanProcessor` so export never sits on the request path.
- **Tests on SQLite** (aiosqlite) via the same ORM; Postgres-only features avoided.

## Data model
See spec. Portable types only (`Uuid`, `String`, `DateTime(timezone=True)`), so the ORM runs on
SQLite in tests and Postgres in compose.

## Risks
- Langfuse v3 is itself OTel based. It gets its own isolated `TracerProvider` so our HTTP/DB spans
  are not shipped to Langfuse and its spans do not pollute Jaeger.
- Metric label cardinality: `model` labels are bounded by the catalog; custom models are labelled
  `byom` by provider and their id, which is user-controlled — capped by per-user custom model limit (10).
