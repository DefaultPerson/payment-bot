from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    PlatformCollector,
    ProcessCollector,
)


@dataclass(frozen=True, slots=True)
class PaymentBotMetrics:
    registry: CollectorRegistry
    payments_total: Counter
    payment_amount_usd_sum: Counter
    subscriptions_active: Gauge
    provider_poll_total: Counter
    broadcast_send_total: Counter
    build_info: Gauge

    def record_payment(
        self,
        *,
        provider: str,
        status: str,
        amount_usd: Decimal | None = None,
    ) -> None:
        self.payments_total.labels(provider=provider, status=status).inc()
        if status == "paid" and amount_usd is not None:
            self.payment_amount_usd_sum.labels(provider=provider).inc(float(amount_usd))

    def record_provider_poll(self, *, provider: str, result: str) -> None:
        self.provider_poll_total.labels(provider=provider, result=result).inc()

    def record_broadcast(self, *, result: str, count: int = 1) -> None:
        self.broadcast_send_total.labels(result=result).inc(count)

    def set_subscriptions_active(self, value: int) -> None:
        self.subscriptions_active.set(value)


def create_metrics() -> PaymentBotMetrics:
    registry = CollectorRegistry(auto_describe=True)
    GCCollector(registry=registry)
    PlatformCollector(registry=registry)
    ProcessCollector(registry=registry)

    payments_total = Counter(
        "payments_total",
        "Payment lifecycle events by provider and status",
        ["provider", "status"],
        registry=registry,
    )
    payment_amount_usd_sum = Counter(
        "payment_amount_usd_sum",
        "Successful paid payment gross amount in USD by provider",
        ["provider"],
        registry=registry,
    )
    subscriptions_active = Gauge(
        "subscriptions_active",
        "Current number of active subscriptions",
        registry=registry,
    )
    provider_poll_total = Counter(
        "provider_poll_total",
        "Provider polling outcomes by provider and result",
        ["provider", "result"],
        registry=registry,
    )
    broadcast_send_total = Counter(
        "broadcast_send_total",
        "Broadcast send outcomes by result",
        ["result"],
        registry=registry,
    )
    build_info = Gauge(
        "payment_bot_build_info",
        "Payment bot build information",
        ["phase"],
        registry=registry,
    )
    build_info.labels(phase="8").set(1)
    return PaymentBotMetrics(
        registry=registry,
        payments_total=payments_total,
        payment_amount_usd_sum=payment_amount_usd_sum,
        subscriptions_active=subscriptions_active,
        provider_poll_total=provider_poll_total,
        broadcast_send_total=broadcast_send_total,
        build_info=build_info,
    )


def create_metrics_registry() -> CollectorRegistry:
    return create_metrics().registry
