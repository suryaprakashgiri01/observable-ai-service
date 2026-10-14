# Tradeoffs

## Logging vs performance

Each LLM attempt writes one INFO line, and each request writes one access line. Bodies are never
logged: questions and answers can be large or sensitive, and they go to Langfuse, which is built
for them. httpx's own per-request INFO lines are dropped, because our `llm call` line already
carries more. Logging is synchronous to stdout. At this volume that costs microseconds; at very
high RPS the next step is a queue-backed handler or sampling of INFO lines, while warnings and
errors are always kept.

## Simplicity vs observability: span per attempt

A single span around the whole LLM step would be simpler. One span per retry attempt costs a few
more spans per request, but it turns "this request took 60 s" into "three 20 s timeouts on one
model, then a fallback". That is the difference between guessing and knowing. Spans are exported
by a `BatchSpanProcessor` on a background thread, so the request path pays only for creating them.
At scale the cost lever is head sampling (e.g. 10%) plus always keeping error traces, not
removing spans.

## Metrics vs development time

Every metric has bounded labels (route template, catalog model id, error code). Adding `user_id`
or the raw path would have been one line and a slow outage later, because each value becomes a
time series. Per-user questions are answered from logs and traces instead.

## One planning call vs a second "answer" call

The tool's answer is templated rather than written by a second LLM call. This halves LLM latency
and cost and keeps business numbers exact, at the price of less natural wording.

## Retry budget vs latency

Three attempts with a 20 s timeout can cost a minute before the fallback runs. The defaults
favour answering; a latency-sensitive deployment would lower `LLM_TIMEOUT_SECONDS` and
`LLM_MAX_ATTEMPTS`, or add a total deadline. The per-attempt spans and
`llm_request_duration_seconds` show where to set those numbers.
