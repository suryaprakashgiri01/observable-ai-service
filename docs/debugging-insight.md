# Debugging insights

Three real issues hit while building this. Each was found through the service's own telemetry.

## 1. The fallback chain had nothing to fall back to

**Symptom.** `make demo-fault` (an injected LLM timeout) returned `503 LLM_UNAVAILABLE` instead of
degrading. The orchestrator is designed so that this should never happen.

**How observability found it.**
- The error envelope carried `request_id=demo-fault-1`. Filtering the JSON logs by it showed three
  `llm call … outcome=timeout` lines, all for `model_id=mock-echo`, and **no** `llm model failed,
  falling back` line.
- `model_selection_total{source="mock"}` had moved: the request never selected a real model,
  because no provider key was configured and the default degraded to the mock.
- In Jaeger the trace had three `llm.call` spans with `llm.model_id=mock-echo` and nothing after them.

**Root cause.** The chain was `[selected, fallback, mock]`, with the mock appended only if it was
not already present. When the *selected* model was the mock and the fault hit it, there was no
link left.

**Fix.** Always keep a last-resort mock after the selected model. A unit test
(`test_fault_on_selected_mock_still_degrades_to_last_resort_mock`) pins the chain to
`["mock-echo", "mock-echo"]`. The same demo now returns 200 with `fallback_used: true`,
`attempts: 4`, about 5 s latency, and the fallback is visible in `llm_fallbacks_total`.

## 2. Database spans silently missing

**Symptom.** Traces in Jaeger showed `llm.call` and HTTP spans but no SQL, so the auth lookup and
ticket insert were invisible inside `/ask`.

**How observability found it.** The startup log, as structured JSON, had one `level=error` line
from `opentelemetry.instrumentation`: *SQLAlchemyInstrumentor only instruments sqlalchemy < 2.1.0,
installed 2.1.4, so nothing can be instrumented.* This was not a crash, only a log line that is
easy to miss unless logs are structured and filtered by level.

**Fix.** Pin `sqlalchemy>=2.0,<2.1`. After the rebuild the same trace showed `connect`,
`SELECT` (×4) and `UPDATE` spans under `POST /ask`.

## 3. Every dashboard number doubled

**Symptom.** Request rate in Grafana was twice what the load generator sent.

**How observability found it.** Prometheus *Status → Targets* listed two healthy targets,
`app:8000` and `host.docker.internal:8000`. They are the same process: the compose app publishes
8000 on the host.

**Fix.** One target, `host.docker.internal:8000`, which works both for `make up` and for
`make dev`.

## What this shows

None of these raised an exception in the request path. Each was visible only because the
request id, the per-attempt spans, and bounded metrics were in place before the bug was.
