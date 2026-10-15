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
