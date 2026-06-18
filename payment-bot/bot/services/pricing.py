from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from bot.config import Settings, TariffConfig
from bot.models import PromoKind, UserStatus
from bot.repos.tariffs import get_tariff

USD_CENTS = Decimal("0.01")
DAYS_PER_MONTH = 30


@dataclass(frozen=True, slots=True)
class PromoSnapshot:
    promo_id: int
    code: str
    kind: PromoKind
    value: Decimal


@dataclass(frozen=True, slots=True)
class TariffQuote:
    tariff: TariffConfig
    status: UserStatus
    duration_days: int
    net_usd: Decimal
    gross_usd: Decimal
    fee_usd: Decimal
    promo_id: int | None = None
    promo_code: str | None = None
    promo_kind: PromoKind | None = None
    promo_value: Decimal | None = None
    promo_discount_usd: Decimal = Decimal("0.00")
    promo_extra_days: int = 0


def money(value: Decimal) -> Decimal:
    return value.quantize(USD_CENTS, rounding=ROUND_HALF_UP)


def tariff_duration_days(months: int) -> int:
    return months * DAYS_PER_MONTH


def quote_tariff(
    settings: Settings,
    code: str,
    status: UserStatus,
    *,
    promo: PromoSnapshot | None = None,
) -> TariffQuote:
    tariff = get_tariff(settings, code)

    base_net = money(_net_amount(settings, tariff, status))
    net, promo_discount_usd, promo_extra_days = _apply_promo(settings, base_net, promo)
    fee_multiplier = Decimal("1") - (settings.cryptobot.fee_percent / Decimal("100"))
    gross = money(net / fee_multiplier)

    return TariffQuote(
        tariff=tariff,
        status=status,
        duration_days=tariff_duration_days(tariff.duration_months),
        net_usd=net,
        gross_usd=gross,
        fee_usd=money(gross - net),
        promo_id=promo.promo_id if promo is not None else None,
        promo_code=promo.code if promo is not None else None,
        promo_kind=promo.kind if promo is not None else None,
        promo_value=promo.value if promo is not None else None,
        promo_discount_usd=promo_discount_usd,
        promo_extra_days=promo_extra_days,
    )


def _net_amount(settings: Settings, tariff: TariffConfig, status: UserStatus) -> Decimal:
    if status is UserStatus.silver:
        return _fixed_status_amount(
            settings.loyalty.silver_monthly_price_usd,
            tariff,
        )
    if status is UserStatus.gold:
        return _fixed_status_amount(
            settings.loyalty.gold_monthly_price_usd,
            tariff,
        )

    net = _apply_percent_discount(tariff.base_price_usd, tariff.duration_discount_percent)
    if status is UserStatus.bronze:
        return _apply_percent_discount(net, settings.loyalty.bronze_discount_percent)
    return net


def _fixed_status_amount(monthly_price: Decimal, tariff: TariffConfig) -> Decimal:
    return _apply_percent_discount(
        monthly_price * tariff.duration_months,
        tariff.duration_discount_percent,
    )


def _apply_percent_discount(value: Decimal, percent: Decimal) -> Decimal:
    return value * (Decimal("1") - (percent / Decimal("100")))


def _apply_promo(
    settings: Settings,
    net: Decimal,
    promo: PromoSnapshot | None,
) -> tuple[Decimal, Decimal, int]:
    if promo is None:
        return net, Decimal("0.00"), 0

    if promo.kind is PromoKind.extra_days:
        return net, Decimal("0.00"), max(0, int(promo.value))

    discount = _promo_discount(settings, net, promo)
    return money(net - discount), discount, 0


def _promo_discount(settings: Settings, net: Decimal, promo: PromoSnapshot) -> Decimal:
    max_discount = net * (settings.promos.max_discount_percent / Decimal("100"))
    if promo.kind is PromoKind.percent:
        requested = net * (promo.value / Decimal("100"))
    elif promo.kind is PromoKind.fixed_usd:
        requested = promo.value
    else:
        requested = Decimal("0")

    max_to_keep_positive = max(Decimal("0"), net - USD_CENTS)
    return money(min(requested, max_discount, max_to_keep_positive))
