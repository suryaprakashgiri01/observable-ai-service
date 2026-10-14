# Tasks: observable-ask-service

Each task is one commit (a large task may carry several). Commit subjects reference the task id.

| ID | Task | Verifies AC |
|---|---|---|
| T01 | Settings (`app/core/config.py`) | — |
| T02 | structlog JSON logging + request contextvars | 9 |
| T03 | Request context middleware (X-Request-ID) + access log | 9 |
| T04 | Error codes, `AppError`, envelope handlers | 3, 4 |
| T05 | Prometheus metrics registry, HTTP metrics middleware, `/metrics` | 10 |
| T06 | OpenTelemetry: provider, OTLP exporter, FastAPI/httpx/SQLAlchemy instrumentation, log correlation | 12 |
| T07 | App factory, `app/main.py`, `/health`, `/health/ready` | 11 |
| T08 | DB engine/session + ORM models | — |
| T09 | Alembic + initial migration | — |
| T10 | Auth: key hashing, `CurrentUser` dependency, auth metrics | 4 |
| T11 | Seed scripts: model catalog, users | — |
| T12 | LLM types + provider protocol + mock provider | — |
| T13 | OpenAI + OpenAI-compatible (BYOM) adapters | — |
| T14 | Anthropic adapter | — |
| T15 | Gemini adapter | — |
| T16 | Model catalog + DB-backed registry (resolution, default/fallback, availability) | 5 |
| T17 | Orchestrator: retry, fallback chain, spans, metrics, Langfuse | 6 |
| T18 | `/models` routes (list, default, fallback, custom register/delete) | 7, 8 |
| T19 | Ticket tool + repository | 1 |
| T20 | Report tool + mock orders | 2 |
| T21 | Agent: JSON plan prompt, parse/repair, tool dispatch | 1, 2 |
| T22 | `POST /ask`, `GET /tickets/{id}`, fault injection | 1, 2, 3, 5 |
| T23 | Tests: core + middleware | 3, 9, 10, 11 |
| T24 | Tests: LLM registry + orchestrator | 5, 6 |
| T25 | Tests: API (auth, ask, models, tickets) | 1–8 |
| T26 | Dockerfile | — |
| T27 | Compose: postgres, app, jaeger, prometheus, grafana + dashboard | 12 |
| T28 | CI workflow | — |
| T29 | Docs: README, architecture, demo script, debugging insight, tradeoffs | — |
