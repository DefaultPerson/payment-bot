from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Gauge,
    GCCollector,
    PlatformCollector,
    ProcessCollector,
)


def create_metrics_registry() -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    GCCollector(registry=registry)
    PlatformCollector(registry=registry)
    ProcessCollector(registry=registry)
    build_info = Gauge(
        "payment_bot_build_info",
        "Payment bot build information",
        ["phase"],
        registry=registry,
    )
    build_info.labels(phase="0").set(1)
    return registry
