from __future__ import annotations

from decimal import Decimal

import pytest

from bot.config import Settings
from bot.models import UserStatus
from bot.services.pricing import TariffQuote, quote_tariff, tariff_duration_days


def test_tariff_duration_days_uses_30_day_months() -> None:
    assert tariff_duration_days(1) == 30
    assert tariff_duration_days(12) == 360


def test_quote_basic_user_applies_duration_discount_and_crypto_fee() -> None:
    settings = Settings()

    quote = quote_tariff(settings, "3m", UserStatus.basic)

    assert isinstance(quote, TariffQuote)
    assert quote.tariff.code == "3m"
    assert quote.status is UserStatus.basic
    assert quote.duration_days == 90
    assert quote.net_usd == Decimal("213.30")
    assert quote.gross_usd == Decimal("219.90")
    assert quote.fee_usd == Decimal("6.60")


def test_quote_bronze_user_applies_duration_discount_and_status_discount() -> None:
    settings = Settings()

    quote = quote_tariff(settings, "3m", UserStatus.bronze)

    assert quote.net_usd == Decimal("191.97")
    assert quote.gross_usd == Decimal("197.91")
    assert quote.fee_usd == Decimal("5.94")


@pytest.mark.parametrize(
    ("status", "expected_net", "expected_gross"),
    [
        (UserStatus.silver, Decimal("195.00"), Decimal("201.03")),
        (UserStatus.gold, Decimal("150.00"), Decimal("154.64")),
    ],
)
def test_quote_fixed_monthly_status_prices_do_not_get_duration_discount(
    status: UserStatus,
    expected_net: Decimal,
    expected_gross: Decimal,
) -> None:
    settings = Settings()

    quote = quote_tariff(settings, "3m", status)

    assert quote.net_usd == expected_net
    assert quote.gross_usd == expected_gross


def test_quote_rejects_unknown_tariff() -> None:
    settings = Settings()

    with pytest.raises(LookupError, match="tariff unknown was not found"):
        quote_tariff(settings, "unknown", UserStatus.basic)


def test_quote_rejects_inactive_tariff() -> None:
    settings = Settings(
        tariffs={
            "items": [
                {
                    "code": "inactive",
                    "duration_months": 1,
                    "base_price_usd": Decimal("79"),
                    "active": False,
                },
            ],
        },
    )

    with pytest.raises(LookupError, match="tariff inactive was not found"):
        quote_tariff(settings, "inactive", UserStatus.basic)
