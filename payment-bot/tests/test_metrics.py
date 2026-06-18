from __future__ import annotations

from decimal import Decimal

from prometheus_client import generate_latest


def test_create_metrics_registers_phase8_collectors_and_records_values() -> None:
    from bot.core.metrics import create_metrics

    metrics = create_metrics()

    metrics.record_payment(provider="cryptobot", status="paid", amount_usd=Decimal("81.44"))
    metrics.record_payment(provider="stars", status="duplicate")
    metrics.record_provider_poll(provider="xrocket", result="success")
    metrics.record_provider_poll(provider="xrocket", result="error")
    metrics.record_broadcast(result="success", count=2)
    metrics.record_broadcast(result="failed")
    metrics.set_subscriptions_active(7)

    body = generate_latest(metrics.registry).decode()

    assert 'payment_bot_build_info{phase="8"} 1.0' in body
    assert 'payments_total{provider="cryptobot",status="paid"} 1.0' in body
    assert 'payments_total{provider="stars",status="duplicate"} 1.0' in body
    assert 'payment_amount_usd_sum_total{provider="cryptobot"} 81.44' in body
    assert 'provider_poll_total{provider="xrocket",result="success"} 1.0' in body
    assert 'provider_poll_total{provider="xrocket",result="error"} 1.0' in body
    assert 'broadcast_send_total{result="success"} 2.0' in body
    assert 'broadcast_send_total{result="failed"} 1.0' in body
    assert "subscriptions_active 7.0" in body


def test_create_metrics_registry_compatibility_wrapper_exposes_required_collectors() -> None:
    from bot.core.metrics import create_metrics_registry

    registry = create_metrics_registry()
    body = generate_latest(registry).decode()

    for metric_name in [
        "payments_total",
        "payment_amount_usd_sum_total",
        "subscriptions_active",
        "provider_poll_total",
        "broadcast_send_total",
        "payment_bot_build_info",
    ]:
        assert f"# HELP {metric_name} " in body
