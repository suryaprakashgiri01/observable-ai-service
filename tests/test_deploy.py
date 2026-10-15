"""Guards on the deploy files: what alerts, and that the dashboard shows every metric."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = (ROOT / "deploy" / "prometheus-alerts.yml").read_text()


def test_only_5xx_and_401_403_http_statuses_alert() -> None:
    filters = set(re.findall(r'http_requests_total\{[^}]*status=~"([^"]+)"', RULES))
    assert filters == {"5..", "401|403"}, filters


def test_every_rule_has_severity_summary_and_dashboard_link() -> None:
    names = re.findall(r"- alert: (\w+)", RULES)
    assert set(names) == {
        "Http5xxErrors",
        "Http4xxAuthErrors",
        "HighErrorRate",
        "LlmFallbacks",
        "AskLatencyHigh",
        "AskJobsFailing",
        "ServiceDown",
    }
    blocks = RULES.split("- alert: ")[1:]
    for block in blocks:
        assert "severity:" in block and "summary:" in block and "viewPanel=" in block, block[:40]


def test_every_metric_family_is_on_the_dashboard() -> None:
    import json

    from app.core.metrics import REGISTRY

    dashboard = json.loads(
        (ROOT / "deploy" / "grafana" / "dashboards" / "ai-service.json").read_text()
    )
    exprs = " ".join(
        t["expr"] for p in dashboard["panels"] for t in p.get("targets", []) if "expr" in t
    )
    families = {metric.name for metric in REGISTRY.collect()}
    # Process metrics come from /proc, so they exist only on Linux (the container, CI).
    families |= {
        "process_cpu_seconds",
        "process_resident_memory_bytes",
        "process_virtual_memory_bytes",
        "process_open_fds",
        "process_max_fds",
        "process_start_time_seconds",
    }
    missing = sorted(f for f in families if f not in exprs)
    assert missing == [], f"not on the dashboard: {missing}"


def test_dashboard_json_is_generated_from_the_builder() -> None:
    import json
    import runpy

    built = runpy.run_path(str(ROOT / "deploy" / "grafana" / "build_dashboard.py"))["build"]()
    on_disk = json.loads(
        (ROOT / "deploy" / "grafana" / "dashboards" / "ai-service.json").read_text()
    )
    assert built == on_disk, "run: uv run python deploy/grafana/build_dashboard.py"


def test_alert_dashboard_links_point_at_real_panels() -> None:
    import json

    ids = {
        p["id"]
        for p in json.loads(
            (ROOT / "deploy" / "grafana" / "dashboards" / "ai-service.json").read_text()
        )["panels"]
    }
    linked = {int(i) for i in re.findall(r"viewPanel=(\d+)", RULES)}
    assert linked <= ids, linked - ids
