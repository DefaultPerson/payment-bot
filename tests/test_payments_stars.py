from __future__ import annotations

from decimal import Decimal

from bot.config import Settings
from bot.models import UserStatus
from bot.payments.stars import (
    STARS_RECURRING_MONTH_SECONDS,
    quote_stars_invoice,
    stars_subscription_period,
)
from bot.services.pricing import quote_tariff


def test_quote_stars_invoice_compensates_app_store_fee_and_rounds_up() -> None:
    settings = Settings(stars={"star_usd_rate": Decimal("0.013"), "app_store_fee_percent": 30})
    quote = quote_tariff(settings, "1m", UserStatus.basic)

    stars = quote_stars_invoice(settings, quote)

    assert stars.amount == 6484
    assert stars.currency == "XTR"
    assert stars.subscription_period == STARS_RECURRING_MONTH_SECONDS
    assert stars.gross_usd == Decimal("84.29")
    assert stars.fee_usd == Decimal("25.29")
    assert stars.net_usd == Decimal("59.00")


def test_quote_stars_invoice_keeps_one_shot_tariffs_non_recurring() -> None:
    settings = Settings()
    quote = quote_tariff(settings, "3m", UserStatus.basic)

    stars = quote_stars_invoice(settings, quote)

    assert stars.subscription_period is None
    assert stars.amount > 0


def test_stars_subscription_period_is_only_for_one_month_tariff() -> None:
    assert stars_subscription_period(1) == STARS_RECURRING_MONTH_SECONDS
    assert stars_subscription_period(3) is None


def test_quote_stars_invoice_honors_amount_override() -> None:
    settings = Settings(
        payments={"test_mode": True},
        tariffs={
            "items": [
                {
                    "code": "test",
                    "duration_months": 3,
                    "base_price_usd": Decimal("0.50"),
                    "test_only": True,
                    "stars_amount_override": 1,
                }
            ]
        },
    )
    quote = quote_tariff(settings, "test", UserStatus.basic)

    stars = quote_stars_invoice(settings, quote)

    assert stars.amount == 1
    assert stars.subscription_period is None
    assert stars.gross_usd == Decimal("0.01")
