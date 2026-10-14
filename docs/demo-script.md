# Demo script (8–10 min)

Setup before recording: `make up && make seed`, `.env` with `SEED_DEMO_API_KEY` (and provider
keys if you want a real model), terminal plus browser tabs for Swagger, Jaeger, Grafana and
Langfuse.

## 1. Live demo (3–4 min)

1. `curl localhost:8000/health` (no auth), then `curl localhost:8000/ask` without a key: `401 AUTH_REQUIRED`.
2. `GET /models` with the key: ten models by tier, `available` and `is_default`/`is_fallback` flags.
3. **Success:** `make demo-success`. Point out `request_id`, `trace_id`, `action.ticket`, the
   `model` block (requested/used/source/attempts), then `GET /tickets/{id}`, whose ticket stores
   the trace id.
4. Pick a model per request: repeat with `-H 'X-Model-Id: claude-haiku-5-5'` (or another
   available model). `model.source = header`.
5. **Invalid:** `make demo-invalid`. Show the `422 VALIDATION_ERROR` envelope carrying `request_id`.
6. **Failing dependency:** `make demo-fault`. It answers after about 5 s with `fallback_used: true`, `attempts: 4`.
7. Jaeger: paste the trace id and walk the tree: `POST /ask` → `model.resolve` → `agent.plan` →
   3 red `llm.call` (timeout) → green `llm.call` → `tool.generate_report`, plus SQL spans.
8. Logs: `docker compose logs app | grep demo-fault-1`. Every line carries the request id; note
   the `llm call failed, retrying` and `falling back` lines.
9. Grafana: request rate, `/ask` p95 jump, *Retries and fallbacks*, *Errors by code*
   (`VALIDATION_ERROR`), *Auth failures*.

## 2. What I built (2–3 min)

Use [architecture.md](architecture.md): middleware stack, auth, registry resolution order, the
orchestrator chain, the tools. Explain why the orchestrator calls providers over raw HTTP, why
the agent plans in JSON, and how a model is chosen per request (header → body → user default →
global default) with tiers.

## 3. Debugging insight (1–2 min)

Use [debugging-insight.md](debugging-insight.md) #1. The fallback chain emptied: found from the
`request_id` in the error, the log lines without a fallback, `model_selection_total{source="mock"}`,
and a trace that stopped after three `llm.call` spans. Then the fix and the test.

## 4. Tradeoff (1–2 min)

Use [tradeoffs.md](tradeoffs.md): **span per attempt** (simplicity vs observability), with
bounded metric labels as the second point.
