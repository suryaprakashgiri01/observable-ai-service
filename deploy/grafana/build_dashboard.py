"""Generate deploy/grafana/dashboards/ai-service.json.

The dashboard is code so it can be reviewed, and so a test can check every
metric family is on it. Panel ids are fixed: alert annotations link to them
(`?viewPanel=<id>`), see deploy/prometheus-alerts.yml.

    uv run python deploy/grafana/build_dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROM = {"type": "prometheus", "uid": "prometheus"}
JOB = 'job="observable-ai-service"'
OUT = Path(__file__).parent / "dashboards" / "ai-service.json"

# Counters use increase() over $__rate_interval: a single event shows as a bar of
# height 1, where rate() of a fresh series would show nothing at all.
Panel = dict[str, Any]


def _targets(exprs: list[tuple[str, str]], *, instant: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "datasource": PROM,
            "expr": expr,
            "legendFormat": legend,
            "refId": chr(65 + i),
            **({"instant": True, "format": "table"} if instant else {}),
        }
        for i, (expr, legend) in enumerate(exprs)
    ]


def ts(
    pid: int,
    title: str,
    exprs: list[tuple[str, str]],
    *,
    unit: str = "short",
    bars: bool = False,
    stack: bool = False,
    w: int = 12,
    desc: str = "",
) -> Panel:
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": desc,
        "datasource": PROM,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "drawStyle": "bars" if bars else "line",
                    "fillOpacity": 60 if bars else (15 if stack else 0),
                    "stacking": {"mode": "normal" if (stack or bars) else "none"},
                    "lineWidth": 1,
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
        "targets": _targets(exprs),
        "gridPos": {"w": w, "h": 8},
    }


def stat(
    pid: int,
    title: str,
    expr: str,
    *,
    unit: str = "short",
    desc: str = "",
    legend: str = "",
    thresholds: list[tuple[str, float | None]] | None = None,
) -> Panel:
    steps = [{"color": c, "value": v} for c, v in (thresholds or [("green", None)])]
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": PROM,
        "fieldConfig": {
            "defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "value",
            "graphMode": "area",
            "textMode": "value_and_name" if legend else "value",
        },
        "targets": _targets([(expr, legend)]),
        "gridPos": {"w": 4, "h": 4},
    }


def table(pid: int, title: str, expr: str, *, desc: str = "") -> Panel:
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "description": desc,
        "datasource": PROM,
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True, "footer": {"show": False}},
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": {
                        "Time": True,
                        "__name__": True,
                        "Value": True,
                        "job": True,
                        "instance": True,
                        "alertstate": True,
                    },
                },
            }
        ],
        "targets": _targets([(expr, "")], instant=True),
        "gridPos": {"w": 12, "h": 8},
    }


def row(pid: int, title: str) -> Panel:
    return {
        "id": pid,
        "type": "row",
        "title": title,
        "collapsed": False,
        "panels": [],
        "gridPos": {"w": 24, "h": 1},
    }


RED = [("green", None), ("red", 1)]
H = f"http_requests_total{{{JOB}}}"

LAYOUT: list[list[Panel]] = [
    [row(100, "Golden signals")],
    [
        stat(1, "Requests / s", f"sum(rate({H}[$__rate_interval]))", unit="reqps"),
        stat(
            2,
            "Error rate (5xx, 5m)",
            f'sum(rate(http_requests_total{{{JOB},status=~"5.."}}[5m])) '
            f"/ clamp_min(sum(rate({H}[5m])), 1e-9)",
            unit="percentunit",
            thresholds=[("green", None), ("orange", 0.01), ("red", 0.05)],
        ),
        stat(
            3,
            "5xx responses (range)",
            f'sum(increase(http_requests_total{{{JOB},status=~"5.."}}[$__range]))',
            thresholds=RED,
            desc="Count over the selected time range. Alerts: Http5xxErrors.",
        ),
        stat(
            4,
            "401 / 403 (range)",
            f'sum(increase(http_requests_total{{{JOB},status=~"401|403"}}[$__range]))',
            thresholds=[("green", None), ("orange", 1)],
            desc="Auth rejections over the range. Alerts: Http4xxAuthErrors.",
        ),
        stat(
            5,
            "/ask p95 latency",
            "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket"
            f'{{{JOB},route="/ask"}}[5m])))',
            unit="s",
            thresholds=[("green", None), ("orange", 1), ("red", 3)],
        ),
        stat(7, "In-flight requests", f"sum(http_requests_in_progress{{{JOB}}})"),
    ],
    [
        ts(
            8,
            "Request rate by route and status",
            [(f"sum by (route, status) (rate({H}[$__rate_interval]))", "{{route}} {{status}}")],
            unit="reqps",
        ),
        ts(
            6,
            "Latency by route (p50 / p95 / p99)",
            [
                (
                    f"histogram_quantile({q}, sum by (le, route) (rate("
                    f"http_request_duration_seconds_bucket{{{JOB}}}[5m])))",
                    f"p{int(q * 100)} {{{{route}}}}",
                )
                for q in (0.5, 0.95, 0.99)
            ],
            unit="s",
        ),
    ],
    [
        ts(
            9,
            "Responses by status class",
            [
                (
                    f'sum by (class) (label_replace(increase({H}[$__rate_interval]), "class", "${{1}}xx", '
                    f'"status", "(.).."))',
                    "{{class}}",
                )
            ],
            bars=True,
            desc="2xx / 4xx / 5xx counts per interval.",
        ),
        ts(
            20,
            "5xx by route and status",
            [
                (
                    f'sum by (method, route, status) (increase(http_requests_total{{{JOB},status=~"5.."}}'
                    f"[$__rate_interval]))",
                    "{{status}} {{method}} {{route}}",
                )
            ],
            bars=True,
            desc="Every 5xx, including the first one (series are pre-created at 0).",
        ),
    ],
    [
        ts(
            21,
            "401 / 403 by route",
            [
                (
                    f"sum by (method, route, status) (increase(http_requests_total{{{JOB},"
                    f'status=~"401|403"}}[$__rate_interval]))',
                    "{{status}} {{method}} {{route}}",
                )
            ],
            bars=True,
        ),
        ts(
            11,
            "Auth failures by reason",
            [
                (
                    f"sum by (reason) (increase(auth_failures_total{{{JOB}}}[$__rate_interval]))",
                    "{{reason}}",
                )
            ],
            bars=True,
        ),
    ],
    [
        ts(
            10,
            "Errors by code",
            [
                (
                    f"sum by (code) (increase(ask_errors_total{{{JOB}}}[$__rate_interval]))",
                    "{{code}}",
                )
            ],
            bars=True,
            w=24,
            desc="Every error envelope rendered, by stable error code.",
        ),
    ],
    [row(101, "Alerts")],
    [
        table(
            60,
            "Firing alerts",
            f'ALERTS{{alertstate="firing",{JOB}}} or ALERTS{{alertstate="firing"}}',
            desc="Alerts Prometheus is firing now. They also arrive in GET /notifications.",
        ),
        ts(
            61,
            "Alert notifications received (webhook)",
            [
                (
                    f"sum by (alertname, status) (increase(alerts_received_total{{{JOB}}}"
                    "[$__rate_interval]))",
                    "{{alertname}} {{status}}",
                )
            ],
            bars=True,
        ),
    ],
    [
        stat(
            62,
            "Unread notifications",
            f"sum(notifications_unread{{{JOB}}})",
            thresholds=[("green", None), ("orange", 1)],
        )
    ],
    [row(102, "LLM orchestrator")],
    [
        ts(
            31,
            "LLM attempts by model and outcome",
            [
                (
                    f"sum by (model, outcome) (increase(llm_requests_total{{{JOB}}}[$__rate_interval]))",
                    "{{model}} {{outcome}}",
                )
            ],
            bars=True,
        ),
        ts(
            32,
            "LLM latency p95 by model",
            [
                (
                    "histogram_quantile(0.95, sum by (le, model) (rate("
                    f"llm_request_duration_seconds_bucket{{{JOB}}}[5m])))",
                    "{{model}}",
                )
            ],
            unit="s",
        ),
    ],
    [
        ts(
            30,
            "Retries and fallbacks",
            [
                (
                    f"sum by (model) (increase(llm_retries_total{{{JOB}}}[$__rate_interval]))",
                    "retry {{model}}",
                ),
                (
                    f"sum by (from_model, to_model, reason) (increase(llm_fallbacks_total{{{JOB}}}"
                    "[$__rate_interval]))",
                    "fallback {{from_model}} → {{to_model}} ({{reason}})",
                ),
            ],
            bars=True,
            desc="Retries climbing before fallbacks is the signature of a degrading provider.",
        ),
        ts(
            33,
            "Tokens by model",
            [
                (
                    f"sum by (model, type) (increase(llm_tokens_total{{{JOB}}}[$__rate_interval]))",
                    "{{model}} {{type}}",
                )
            ],
            stack=True,
        ),
    ],
    [
        ts(
            34,
            "Model selection source",
            [
                (
                    f"sum by (source) (increase(model_selection_total{{{JOB}}}[$__rate_interval]))",
                    "{{source}}",
                )
            ],
            bars=True,
        ),
        ts(
            35,
            "/ask outcomes by intent",
            [
                (
                    f"sum by (intent, outcome) (increase(ask_requests_total{{{JOB}}}[$__rate_interval]))",
                    "{{intent}} {{outcome}}",
                )
            ],
            bars=True,
        ),
    ],
    [row(103, "Async jobs & Kafka")],
    [
        ts(
            40,
            "Async job transitions by status",
            [
                (
                    f"sum by (status) (increase(ask_jobs_total{{{JOB}}}[$__rate_interval]))",
                    "{{status}}",
                )
            ],
            bars=True,
            desc="queued → running → retrying / fallback → completed | failed.",
        ),
        ts(
            42,
            "Kafka publishes by topic and outcome",
            [
                (
                    f"sum by (topic, outcome) (increase(kafka_publish_total{{{JOB}}}[$__rate_interval]))",
                    "{{topic}} {{outcome}}",
                )
            ],
            bars=True,
            desc="Empty while KAFKA_ENABLED=false (feature flag).",
        ),
    ],
    [stat(41, "Jobs in flight", f"sum(ask_jobs_in_flight{{{JOB}}})")],
    [row(104, "Business actions")],
    [
        ts(
            43,
            "Tool calls by outcome",
            [
                (
                    f"sum by (tool, outcome) (increase(agent_tool_calls_total{{{JOB}}}[$__rate_interval]))",
                    "{{tool}} {{outcome}}",
                )
            ],
            bars=True,
        ),
        ts(
            44,
            "Tickets created by priority",
            [
                (
                    f"sum by (priority) (increase(tickets_created_total{{{JOB}}}[$__rate_interval]))",
                    "{{priority}}",
                )
            ],
            bars=True,
        ),
    ],
    [row(105, "Runtime")],
    [
        stat(
            50,
            "Service up",
            f"up{{{JOB}}}",
            thresholds=[("red", None), ("green", 1)],
            desc="0 = Prometheus cannot scrape /metrics. Alerts: ServiceDown.",
        ),
        stat(56, "Uptime", f"time() - process_start_time_seconds{{{JOB}}}", unit="s"),
        stat(55, "Python", f"python_info{{{JOB}}}", legend="{{version}}"),
        stat(
            57,
            "Open fds / max",
            f"process_open_fds{{{JOB}}} / process_max_fds{{{JOB}}}",
            unit="percentunit",
        ),
    ],
    [
        ts(
            51,
            "CPU",
            [(f"rate(process_cpu_seconds_total{{{JOB}}}[$__rate_interval])", "cpu")],
            unit="percentunit",
            w=8,
        ),
        ts(
            52,
            "Memory",
            [
                (f"process_resident_memory_bytes{{{JOB}}}", "resident"),
                (f"process_virtual_memory_bytes{{{JOB}}}", "virtual"),
            ],
            unit="bytes",
            w=8,
        ),
        ts(
            54,
            "Python GC",
            [
                (
                    f"sum by (generation) (rate(python_gc_collections_total{{{JOB}}}[$__rate_interval]))",
                    "collections gen {{generation}}",
                ),
                (
                    f"sum(rate(python_gc_objects_collected_total{{{JOB}}}[$__rate_interval]))",
                    "collected",
                ),
                (
                    f"sum(rate(python_gc_objects_uncollectable_total{{{JOB}}}[$__rate_interval]))",
                    "uncollectable",
                ),
            ],
            w=8,
        ),
    ],
]


def build() -> dict[str, Any]:
    panels: list[Panel] = []
    y = 0
    for line in LAYOUT:
        x = 0
        height = 0
        for panel in line:
            panel["gridPos"].update({"x": x, "y": y})
            x += panel["gridPos"]["w"]
            height = max(height, panel["gridPos"]["h"])
            panels.append(panel)
        y += height
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids)), "duplicate panel id"
    return {
        "uid": "observable-ai-service",
        "title": "Observable AI Service",
        "tags": ["ai", "observability"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 2,
        "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "panels": panels,
        "links": [
            {
                "title": "Jaeger",
                "url": "http://localhost:16686/search?service=observable-ai-service",
                "type": "link",
                "targetBlank": True,
            },
            {
                "title": "Alertmanager",
                "url": "http://localhost:9093",
                "type": "link",
                "targetBlank": True,
            },
            {
                "title": "Prometheus alerts",
                "url": "http://localhost:9090/alerts",
                "type": "link",
                "targetBlank": True,
            },
        ],
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT} ({len(build()['panels'])} panels)")
