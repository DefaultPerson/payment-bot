from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge


def create_metrics_registry() -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    build_info = Gauge(
        "payment_bot_build_info",
        "Payment bot build information",
        ["phase"],
        registry=registry,
    )
    build_info.labels(phase="0").set(1)
    return registry
