from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from bot.config import Settings, TariffConfig
from bot.models import UserStatus
from bot.repos.tariffs import get_tariff

USD_CENTS = Decimal("0.01")
DAYS_PER_MONTH = 30


@dataclass(frozen=True, slots=True)
class TariffQuote:
    tariff: TariffConfig
    status: UserStatus
    duration_days: int
    net_usd: Decimal
    gross_usd: Decimal
    fee_usd: Decimal


def money(value: Decimal) -> Decimal:
    return value.quantize(USD_CENTS, rounding=ROUND_HALF_UP)


def tariff_duration_days(months: int) -> int:
    return months * DAYS_PER_MONTH


def quote_tariff(settings: Settings, code: str, status: UserStatus) -> TariffQuote:
    tariff = get_tariff(settings, code)

    net = money(_net_amount(settings, tariff, status))
    fee_multiplier = Decimal("1") - (settings.cryptobot.fee_percent / Decimal("100"))
    gross = money(net / fee_multiplier)

    return TariffQuote(
        tariff=tariff,
        status=status,
        duration_days=tariff_duration_days(tariff.duration_months),
        net_usd=net,
        gross_usd=gross,
        fee_usd=money(gross - net),
    )


def _net_amount(settings: Settings, tariff: TariffConfig, status: UserStatus) -> Decimal:
    if status is UserStatus.silver:
        return _fixed_status_amount(
            settings.tariffs.silver_monthly_price_usd,
            tariff,
        )
    if status is UserStatus.gold:
        return _fixed_status_amount(
            settings.tariffs.gold_monthly_price_usd,
            tariff,
        )

    net = _apply_percent_discount(tariff.base_price_usd, tariff.duration_discount_percent)
    if status is UserStatus.bronze:
        return _apply_percent_discount(net, settings.tariffs.bronze_discount_percent)
    return net


def _fixed_status_amount(monthly_price: Decimal, tariff: TariffConfig) -> Decimal:
    return _apply_percent_discount(
        monthly_price * tariff.duration_months,
        tariff.duration_discount_percent,
    )


def _apply_percent_discount(value: Decimal, percent: Decimal) -> Decimal:
    return value * (Decimal("1") - (percent / Decimal("100")))
