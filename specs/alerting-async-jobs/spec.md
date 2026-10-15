# Spec: alerting-async-jobs

Status: approved · Owner: Surya Prakash Giri · Ticket: none (personal repo; see `tasks.md`)
Extends: [observable-ask-service](../observable-ask-service/spec.md)

## Problem
1. A first 5xx never shows in Grafana, and nothing alerts on it.
2. Callers of slow, real LLMs block on `/ask`; there is no way to follow an in-flight request.
3. The fallback demo uses one failing model, which under-shows a real multi-hop degradation.
4. Ticket ids are sequential integers (guessable, and inconsistent with every other id).
5. Generating, demonstrating and resetting telemetry by hand is slow and error-prone.

## Goals
- Alerts on 5xx, error rate, **401/403**, fallbacks, latency, failing jobs and service down,
  delivered **in the app** as notifications. Optionally also to Kafka. Every other 4xx (422,
  400, 404, 409) is logged and counted only, never alerted.
- Opt-in **async `/ask`** with persisted status transitions, optionally streamed to Kafka.
- A **feature flag** `KAFKA_ENABLED` (default off). With it off, nothing connects to Kafka.
- A fallback chain of several mock models for the timeout demo.
- **UUID** primary keys for tickets, plus a reversible migration.
- Every metric family visible in Grafana; the first event of any series visible.
- Scripts: traffic generator, cleanup (DB, Prometheus, Jaeger, Alertmanager, Kafka, Langfuse
  local traces), coloured API walkthrough.

## Non-goals
Email/Slack receivers; a Kafka consumer service; exactly-once delivery; job cancellation.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/ask` + `Prefer: respond-async` | key | 202 `{job_id, status, status_url}` |
| GET | `/ask/jobs/{job_id}` | key (owner) | job status, attempts, current model, result or error |
| POST | `/alerts/webhook` | `Authorization: Bearer $ALERT_WEBHOOK_TOKEN` | Alertmanager receiver |
| GET | `/notifications?status=&unread=&limit=` | key | newest first |
| POST | `/notifications/{id}/read` | key | mark read |
| GET | `/tickets/{ticket_id}` | key | `ticket_id` is now a UUID |

New request header on `/ask`: `X-Debug-Fault-Count` (int ≥ 1, default 1, only with
`X-Debug-Fault`): the fault hits the first N models of the chain.

### Job statuses
`queued → running → (retrying | fallback)* → completed | failed`. Every transition updates the
row and, when the flag is on, publishes one JSON event to `ai-service.ask-status`
(key = job_id; headers carry request_id, session_id, traceparent):
`{job_id, status, attempt, model_id, reason?, at, request_id, session_id, trace_id}`.

### Notifications
Alertmanager v4 webhook payload. One row per alert (fingerprint + starts_at): firing, then
updated to resolved. When the flag is on, published to `ai-service.notifications`.

## Fallback chain
selected → user fallback → `LLM_FALLBACK_CHAIN` (default `mock-standard,mock-economy`) →
`mock-echo`, deduplicated. New mocks: `mock-economy`, `mock-standard`, `mock-premium`
(simulated latency).

## Errors
New codes: `JOB_NOT_FOUND` (404), `NOTIFICATION_NOT_FOUND` (404), `WEBHOOK_UNAUTHORIZED` (401).

## Metrics (new)
`alerts_received_total{alertname,status}`, `notifications_unread`, `ask_jobs_total{status}`,
`ask_jobs_in_flight`, `kafka_publish_total{topic,outcome}`, plus process/platform/GC collectors.
All bounded label sets are initialised to 0 at startup.

## Acceptance criteria
1. A single 502 shows in Grafana's 5xx panels on the next scrape. *(manual + pre-init test)*
2a. Within ~30 s of a 401 or 403, `Http4xxAuthErrors` fires and becomes a notification; a 422
   or 404 fires nothing. *(rule file test + manual)*
2. Within ~30 s of a 502, `Http5xxErrors` fires; `GET /notifications` returns it; it resolves
   later. *(webhook tests + manual)*
3. `X-Model-Id: mock-premium`, `llm_timeout`, count 2 → 200, chain
   `[mock-premium, mock-standard, mock-economy]`, attempts 7. *(test)*
4. `Prefer: respond-async` → 202; job reaches `completed` with the same result shape as sync;
   status events are recorded in order. Another user gets 404 on the job. *(test)*
5. `KAFKA_ENABLED=false` → no Kafka client is created; `true` → events published (fake producer
   in tests; Kafka UI manually).
6. Tickets have UUID ids; `alembic upgrade head` / `downgrade -1` / `upgrade head` all succeed
   on Postgres with data present. *(manual run + model test)*
7. Every metric family in the registry is referenced by a dashboard panel. *(test)*
8. Scripts run on macOS and Windows (Python), with `--help`.
