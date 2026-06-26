from __future__ import annotations

from decimal import Decimal

import pytest

from bot.config import Settings
from bot.models import PromoKind, UserStatus
from bot.services.pricing import PromoSnapshot, TariffQuote, quote_tariff, tariff_duration_days


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
    assert quote.net_usd == Decimal("168.15")
    assert quote.gross_usd == Decimal("173.35")
    assert quote.fee_usd == Decimal("5.20")


def test_quote_can_use_xrocket_fee_percent_without_changing_default_crypto_quote() -> None:
    settings = Settings(xrocket={"fee_percent": Decimal("5")})

    quote = quote_tariff(
        settings,
        "1m",
        UserStatus.basic,
        fee_percent=settings.xrocket.fee_percent,
    )
    default_quote = quote_tariff(settings, "1m", UserStatus.basic)

    assert quote.net_usd == Decimal("59.00")
    assert quote.gross_usd == Decimal("62.11")
    assert quote.fee_usd == Decimal("3.11")
    assert default_quote.gross_usd == Decimal("60.82")


def test_quote_bronze_user_applies_duration_discount_and_status_discount() -> None:
    settings = Settings()

    quote = quote_tariff(settings, "3m", UserStatus.bronze)

    assert quote.net_usd == Decimal("151.34")
    assert quote.gross_usd == Decimal("156.02")
    assert quote.fee_usd == Decimal("4.68")


@pytest.mark.parametrize(
    ("status", "expected_net", "expected_gross"),
    [
        (UserStatus.silver, Decimal("185.25"), Decimal("190.98")),
        (UserStatus.gold, Decimal("142.50"), Decimal("146.91")),
    ],
)
def test_quote_fixed_monthly_status_prices_apply_duration_discount(
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


def test_quote_applies_percent_promo_after_loyalty_discounts() -> None:
    settings = Settings()
    promo = PromoSnapshot(
        promo_id=1,
        code="SAVE20",
        kind=PromoKind.percent,
        value=Decimal("20"),
    )

    quote = quote_tariff(settings, "3m", UserStatus.bronze, promo=promo)

    assert quote.net_usd == Decimal("121.07")
    assert quote.promo_discount_usd == Decimal("30.27")
    assert quote.promo_extra_days == 0
    assert quote.gross_usd == Decimal("124.81")


def test_quote_caps_fixed_usd_promo_discount() -> None:
    settings = Settings(promos={"max_discount_percent": Decimal("50")})
    promo = PromoSnapshot(
        promo_id=2,
        code="HALF",
        kind=PromoKind.fixed_usd,
        value=Decimal("500"),
    )

    quote = quote_tariff(settings, "1m", UserStatus.basic, promo=promo)

    assert quote.net_usd == Decimal("29.50")
    assert quote.promo_discount_usd == Decimal("29.50")
    assert quote.gross_usd == Decimal("30.41")


def test_quote_extra_days_promo_preserves_price_and_returns_bonus_days() -> None:
    settings = Settings()
    promo = PromoSnapshot(
        promo_id=3,
        code="BONUS7",
        kind=PromoKind.extra_days,
        value=Decimal("7"),
    )

    quote = quote_tariff(settings, "1m", UserStatus.basic, promo=promo)

    assert quote.net_usd == Decimal("59.00")
    assert quote.promo_discount_usd == Decimal("0.00")
    assert quote.promo_extra_days == 7
    assert quote.gross_usd == Decimal("60.82")
