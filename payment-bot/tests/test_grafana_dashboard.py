from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def test_phase8_grafana_dashboard_contains_required_metric_expressions() -> None:
    dashboard_path = (
        Path(__file__).resolve().parents[1]
        / "ops"
        / "grafana"
        / "payment-bot-dashboard.json"
    )

    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    expressions = set(_iter_exprs(dashboard))

    assert dashboard["title"] == "Hydra Payment Bot"
    assert {
        "sum by (provider, status) (rate(payments_total[5m]))",
        "sum by (provider) (increase(payment_amount_usd_sum_total[1h]))",
        "subscriptions_active",
        "sum by (provider, result) (rate(provider_poll_total[5m]))",
        "sum by (result) (rate(broadcast_send_total[5m]))",
        'payment_bot_build_info{phase="8"}',
    }.issubset(expressions)


def test_phase8_grafana_dashboard_panel_ids_are_unique() -> None:
    dashboard_path = (
        Path(__file__).resolve().parents[1]
        / "ops"
        / "grafana"
        / "payment-bot-dashboard.json"
    )

    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    panel_ids = [panel["id"] for panel in dashboard["panels"]]

    assert len(panel_ids) >= 6
    assert len(panel_ids) == len(set(panel_ids))


def _iter_exprs(value: Any) -> list[str]:
    if isinstance(value, dict):
        expressions = []
        if isinstance(value.get("expr"), str):
            expressions.append(value["expr"])
        for child in value.values():
            expressions.extend(_iter_exprs(child))
        return expressions
    if isinstance(value, list):
        expressions = []
        for child in value:
            expressions.extend(_iter_exprs(child))
        return expressions
    return []
