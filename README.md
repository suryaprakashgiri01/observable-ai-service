# observable-ai-service

An AI-backed REST API built to be **debugged in production**. `POST /ask` answers a question
through an LLM-planned agent that can **open support tickets** and **generate order reports**.
Every request can be followed through structured logs, Prometheus metrics and OpenTelemetry
traces, joined by one `request_id` and one `trace_id`.

The LLM layer is a small in-repo **orchestrator** over OpenAI, Anthropic, Gemini, any
OpenAI-compatible endpoint (bring your own model), and a rule-based mock. Callers pick the model
per request. Failed calls are retried, then the request falls back to a cheaper model, then to
the mock. A provider outage therefore degrades `/ask` instead of failing it.

```
client ──▶ FastAPI ──▶ auth (X-API-Key → Postgres) ──▶ /ask ──▶ agent
                                                              ├─ model.resolve   header → body → user default → global default
                                                              ├─ agent.plan      LLM call(s), retry + fallback chain
                                                              │    └─ llm.call ×N ─▶ OpenAI | Anthropic | Gemini | BYOM | mock
                                                              └─ tool.*          create_ticket (Postgres) | generate_report (mock data)
logs ─▶ stdout JSON      metrics ─▶ /metrics ◀─ Prometheus ◀─ Grafana      traces ─▶ OTLP ─▶ Jaeger      LLM calls ─▶ Langfuse
```

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), and Docker or Podman with a compose provider.

```bash
cp .env.example .env          # optional: add OPENAI/ANTHROPIC/GEMINI keys, Langfuse keys
make install                  # venv + pre-commit hooks
make up                       # app :8000, postgres :5433, jaeger :16686, prometheus :9090,
                              # alertmanager :9093, grafana :3000
make seed                     # model catalog + users (admin, demo, analyst); prints any generated keys
make demo ARGS="--pause"      # coloured walkthrough of the API, with links to every UI
```

Optional: `make up-kafka` (broker :9094, Kafka UI :8080) and `KAFKA_ENABLED=true` in `.env`.

Set `SEED_DEMO_API_KEY` in `.env` before `make seed` if you want a fixed demo key. With no
provider keys everything still runs on `mock-echo`.

| URL | What |
|---|---|
| http://localhost:8000/docs | OpenAPI / Swagger: **Authorize** once, or fill the `X-API-Key` field per request |
| http://localhost:16686 | Jaeger: search service `observable-ai-service` |
| http://localhost:3000 | Grafana → *AI Service / Observable AI Service* |
| http://localhost:9090 | Prometheus (`/alerts` for rule state) |
| http://localhost:9093 | Alertmanager |
| http://localhost:8080 | Kafka UI (only with `make up-kafka`) |

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/ask` | key | Ask; may create a ticket or build a report. `Prefer: respond-async` → 202 + job |
| GET | `/ask/jobs/{job_id}` | key | Async job: status, attempts, every transition, result |
| GET | `/tickets/{id}` | key | Read back one of your tickets (UUID) |
| GET | `/notifications?status=&unread=` | key | Alerts received from Alertmanager |
| POST | `/notifications/{id}/read` | key | Mark one read |
| POST | `/alerts/webhook` | bearer | Alertmanager receiver (not for users) |
| GET | `/models?tier=&provider=` | key | Catalog by tier, with `available`, `is_default`, `is_fallback` |
| PUT | `/models/default` | key | Set *your* default model |
| PUT | `/models/fallback` | key | Set *your* fallback model |
| POST | `/models/custom` | key | Register a bring-your-own OpenAI-compatible model |
| DELETE | `/models/custom/{id}` | key | Remove it |
| GET | `/health`, `/health/ready` | – | Liveness; readiness (database) |
| GET | `/metrics` | – | Prometheus exposition |

```bash
curl -s localhost:8000/ask \
  -H "X-API-Key: $KEY" -H 'X-Request-ID: demo-1' -H 'X-Model-Id: claude-haiku-5-5' \
  -H 'Content-Type: application/json' \
  -d '{"question": "Create a high priority ticket: checkout page is slow"}'
```

Request headers: `X-API-Key` (required), `X-Model-Id` (optional, wins over the body's
`model_id`), `X-Request-ID` (optional, echoed back; generated when absent), `X-Session-ID`
(optional; groups requests into one session in logs, Jaeger and Langfuse; defaults to the
request id), `traceparent` (optional, W3C), `Prefer: respond-async` (optional, async job),
and, demo only (`ENABLE_FAULT_INJECTION=true`), `X-Debug-Fault: llm_timeout | llm_error |
tool_error` with `X-Debug-Fault-Count: N` (the first N models of the fallback chain fail).

Every error has the same shape: `{"error": {"code", "message", "request_id", "details?"}}`.

### Model catalog

| Provider | economy | standard | premium |
|---|---|---|---|
| OpenAI | `gpt-4.1-nano` | `gpt-4.1-mini` | `gpt-4.1` |
| Anthropic | `claude-haiku-5-5` | `claude-sonnet-5-5` | `claude-opus-5-5` |
| Google | `gemini-2.5-flash-lite` | `gemini-2.5-flash` | `gemini-2.5-pro` |
| Mock | `mock-economy` | `mock-standard` | `mock-premium` |
| Mock (last resort) | `mock-echo`, always appended at the end of every fallback chain | | |
| BYOM | anything you register (Ollama, vLLM, OpenRouter…) | | |

## Observability

| Signal | Where | What to look for |
|---|---|---|
| **Logs** | stdout, one JSON object per line | Every line carries `request_id`, `trace_id`, `span_id`, `user_id`. One `request completed` access line per request. API keys are redacted. |
| **Metrics** | `/metrics` → Prometheus → Grafana | RED per route; `llm_requests_total{model,outcome}`, `llm_retries_total`, `llm_fallbacks_total{from_model,to_model,reason}`, `llm_tokens_total`, `model_selection_total{source}`, `ask_errors_total{code}`, `auth_failures_total{reason}`, tool and ticket counters |
| **Traces** | OTLP → Jaeger | `POST /ask` → `model.resolve` → `agent.plan` → `llm.call` (one per attempt) → `tool.*`, plus HTTP client spans and every SQL statement |
| **LLM traces** | Langfuse (when keys set) | One `ask` trace per request with user and session, and one nested generation per LLM attempt with tokens. It uses the **same trace id** as Jaeger. Sessions group requests sent with the same `X-Session-ID`. |
| **Alerts** | Prometheus rules → Alertmanager → `POST /alerts/webhook` → `GET /notifications` | `Http5xxErrors`, `Http4xxAuthErrors` (401/403 only; other 4xx are logged, not alerted), `HighErrorRate`, `LlmFallbacks`, `AskLatencyHigh`, `AskJobsFailing`, `ServiceDown`. Optionally also published to Kafka. |

Every bounded metric series is created at 0 at startup, so the first 5xx (or the first of any
error code) shows in Grafana immediately. Without that, `rate()`/`increase()` cannot see a
series whose first sample is already 1.

The fallback chain is: selected model → your fallback → `LLM_FALLBACK_CHAIN`
(default `mock-standard,mock-economy`) → `mock-echo`.

### Async jobs and Kafka

`POST /ask` with `Prefer: respond-async` returns **202** and a `job_id`. The job runs in the
background, inside the same trace and session. Every status change
(`queued → running → retrying / fallback → completed | failed`) is stored on the job, so
`GET /ask/jobs/{id}` shows it in flight. With the feature flag `KAFKA_ENABLED=true`, job
transitions also go to `ai-service.ask-status` and alerts to `ai-service.notifications`.
With the flag off, nothing connects to Kafka.

The trace id is returned in every `/ask` response and stored on every ticket. That gives one
path from a customer complaint ("this ticket looks wrong") to its request, its logs and its trace.

### Engineering improvement: distributed tracing + retry/fallback

**OpenTelemetry tracing** with one span per *attempt*, not per request. A slow `/ask` reads in
Jaeger as "three 20 s timeouts on `gpt-4.1`, then `mock-echo` answered", not as one opaque 60 s
bar. Spans carry `gen_ai.*` attributes (provider, model, tokens) and the outcome of each attempt.
Log lines carry the same `trace_id`, so you can jump between the two. See
[docs/architecture.md](docs/architecture.md).

## Development

```bash
make dev          # app with reload, against `make up-infra`
make check        # ruff, mypy --strict, pytest with an 80% coverage gate (what CI runs)
make demo-success # / demo-invalid / demo-fault: the three demo requests
make demo         # guided walkthrough with coloured logs and links (ARGS="--pause")
make traffic      # move every dashboard metric (ARGS="--rounds 5 --outage")
make cleanup      # reset DB rows, metrics, traces, alerts, Kafka, Langfuse local traces
                  # (ARGS="--dry-run" to only count, "--yes" to skip the questions)
make dashboard    # regenerate the Grafana JSON from deploy/grafana/build_dashboard.py
make migrate      # alembic upgrade head;  make downgrade = one step back
make help
```

Spec-driven: [spec](specs/observable-ask-service/spec.md) ·
[plan](specs/observable-ask-service/plan.md) · [tasks](specs/observable-ask-service/tasks.md).
Write-ups: [architecture](docs/architecture.md) · [demo script](docs/demo-script.md) ·
[debugging insight](docs/debugging-insight.md) · [tradeoffs](docs/tradeoffs.md).
