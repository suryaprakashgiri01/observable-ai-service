# Plan: alerting-async-jobs

Implements [spec.md](spec.md).

```
Prometheus ──rules──▶ Alertmanager ──webhook──▶ POST /alerts/webhook ─▶ notifications (PG)
                                                             └──(flag)──▶ Kafka ai-service.notifications
POST /ask (Prefer: respond-async) ─▶ ask_jobs (PG, queued) ─▶ 202
        └─ background task (child span, same trace/session/Langfuse trace)
             └─ orchestrator status hook: running / retrying / fallback / completed / failed
                  ├─ update ask_jobs
                  └─(flag)─▶ Kafka ai-service.ask-status
```

## Decisions
- **Alertmanager → webhook, not Grafana alerting.** Rules live in version-controlled files next
  to the metrics. Alertmanager handles grouping, repeats and resolves.
- **Kafka is optional fan-out, not the source of truth.** Postgres holds notifications and job
  status, so the feature works with Kafka off and the API never depends on the broker.
  A publish failure is logged and counted, never raised.
- **In-process background tasks, not a queue worker.** Enough for a single replica and the demo.
  Jobs running at shutdown get a short grace period; leftovers are marked `failed: interrupted`
  on the next start. A multi-replica deployment would consume jobs from Kafka instead.
- **Status hook via contextvar.** The orchestrator stays unaware of jobs; the job runner sets a
  hook that the orchestrator calls on attempt, retry and fallback.
- **Pre-initialised series.** Fixes the first-sample blind spot of `rate()`/`increase()`.
- **Dashboard from a generator script**, so panels are reviewable code and a test can check that
  every metric family is on the dashboard.
- **UUIDs via `gen_random_uuid()`** in the migration (built into Postgres 13+).
