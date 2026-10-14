"""Business action: an order report over mock data."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

ReportMetric = Literal["revenue", "orders", "average_order_value"]
_METRICS: frozenset[str] = frozenset({"revenue", "orders", "average_order_value"})
_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "mock_orders.json"


class ReportOut(BaseModel):
    metric: ReportMetric
    period_days: int
    value: float
    order_count: int
    by_region: dict[str, float]
    top_products: list[dict[str, Any]]
    excluded_refunded: int


@lru_cache(maxsize=1)
def _orders() -> list[dict[str, Any]]:
    data: dict[str, Any] = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    orders: list[dict[str, Any]] = data["orders"]
    return orders


def _value(metric: str, amounts: list[float]) -> float:
    if metric == "orders":
        return float(len(amounts))
    if metric == "average_order_value":
        return round(sum(amounts) / len(amounts), 2) if amounts else 0.0
    return round(sum(amounts), 2)


def generate_report(*, metric: str, period_days: int) -> ReportOut:
    chosen: ReportMetric = metric if metric in _METRICS else "revenue"  # type: ignore[assignment]
    days = max(1, min(int(period_days), 90))
    window = [o for o in _orders() if o["days_ago"] < days]
    counted = [o for o in window if o["status"] != "refunded"]

    regions: defaultdict[str, list[float]] = defaultdict(list)
    revenue_by_product: Counter[str] = Counter()
    for order in counted:
        regions[order["region"]].append(order["amount"])
        revenue_by_product[order["product"]] += order["amount"]

    return ReportOut(
        metric=chosen,
        period_days=days,
        value=_value(chosen, [o["amount"] for o in counted]),
        order_count=len(counted),
        by_region={r: _value(chosen, a) for r, a in sorted(regions.items())},
        top_products=[
            {"product": p, "revenue": round(v, 2)} for p, v in revenue_by_product.most_common(3)
        ],
        excluded_refunded=len(window) - len(counted),
    )
