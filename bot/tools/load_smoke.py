from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import TextIO

from prometheus_client import generate_latest

from bot.core.metrics import create_metrics


@dataclass(frozen=True, slots=True)
class LoadSmokeSummary:
    iterations: int
    payments_recorded: int
    provider_polls_recorded: int
    broadcast_recipients_recorded: int
    subscriptions_active: int
    metrics_bytes: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def run_load_smoke(*, iterations: int = 1000) -> LoadSmokeSummary:
    if iterations < 1:
        msg = "iterations must be positive"
        raise ValueError(msg)

    metrics = create_metrics()
    payments_recorded = 0
    provider_polls_recorded = 0
    broadcast_recipients_recorded = 0
    payment_providers = ("cryptobot", "xrocket", "stars")
    payment_statuses = ("paid", "duplicate", "error")

    for index in range(iterations):
        provider = payment_providers[index % len(payment_providers)]
        status = payment_statuses[index % len(payment_statuses)]
        metrics.record_payment(
            provider=provider,
            status=status,
            amount_usd=Decimal("10.00") if status == "paid" else None,
        )
        payments_recorded += 1

        poll_result = "success" if index % 2 == 0 else "error"
        for poll_provider in ("cryptobot", "xrocket"):
            metrics.record_provider_poll(provider=poll_provider, result=poll_result)
            provider_polls_recorded += 1

        metrics.record_broadcast(result="success", count=1)
        metrics.record_broadcast(result="failed", count=1)
        broadcast_recipients_recorded += 2

    metrics.set_subscriptions_active(iterations)
    metrics_body = generate_latest(metrics.registry)
    return LoadSmokeSummary(
        iterations=iterations,
        payments_recorded=payments_recorded,
        provider_polls_recorded=provider_polls_recorded,
        broadcast_recipients_recorded=broadcast_recipients_recorded,
        subscriptions_active=iterations,
        metrics_bytes=len(metrics_body),
    )


def main(argv: Sequence[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description="Run local payment-bot metric smoke load.")
    parser.add_argument(
        "--iterations",
        type=int,
        default=1000,
        help="Number of local metric iterations to execute.",
    )
    args = parser.parse_args(argv)
    summary = run_load_smoke(iterations=args.iterations)
    stdout.write(json.dumps(summary.as_dict(), sort_keys=True))
    stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
