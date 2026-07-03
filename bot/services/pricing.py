from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from bot.config import Settings, TariffConfig
from bot.models import PromoKind, UserStatus
from bot.repos.tariffs import default_plan, get_plan_tariffs, get_tariff

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


@dataclass(frozen=True, slots=True)
class PricedTariff:
    """A duration tier priced for the buy keyboard: ``old_usd`` is the loyalty+duration price
    before any promo, ``new_usd`` is the price after the pending promo. When
    ``promo_discount_usd`` is 0 the two are equal (no promo / extra-days promo)."""

    tariff: TariffConfig
    old_usd: Decimal
    new_usd: Decimal
    promo_discount_usd: Decimal


def money(value: Decimal) -> Decimal:
    return value.quantize(USD_CENTS, rounding=ROUND_HALF_UP)


def tariff_duration_days(months: int) -> int:
    return months * DAYS_PER_MONTH


def plan_daily_rate(settings: Settings, plan_code: str | None) -> Decimal | None:
    """List price per day for a plan, or ``None`` if it has no active priced item.

    Uses the plan's SHORTEST active item's undiscounted ``base_price_usd`` divided by its
    duration in days — a deterministic list rate that compares two plans on the same basis
    (ignoring duration discounts). ``plan_code=None`` is treated as the default plan.
    """
    if plan_code is None:
        try:
            plan_code = default_plan(settings).code
        except LookupError:
            return None
    items = get_plan_tariffs(settings, plan_code)
    if not items:
        return None
    item = min(items, key=lambda tariff: tariff.duration_months)
    days = tariff_duration_days(item.duration_months)
    if days <= 0:
        return None
    return item.base_price_usd / Decimal(days)


def quote_tariff(
    settings: Settings,
    code: str,
    status: UserStatus,
    *,
    promo: PromoSnapshot | None = None,
    fee_percent: Decimal | None = None,
) -> TariffQuote:
    tariff = get_tariff(settings, code)

    base_net = money(_net_amount(settings, tariff, status))
    net, promo_discount_usd, promo_extra_days = _apply_promo(settings, base_net, promo)
    provider_fee_percent = settings.cryptobot.fee_percent if fee_percent is None else fee_percent
    fee_multiplier = Decimal("1") - (provider_fee_percent / Decimal("100"))
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
    # Duration discount first, then the loyalty-status percentage on top — every tier is a
    # percentage discount off the base price.
    net = _apply_percent_discount(tariff.base_price_usd, tariff.duration_discount_percent)
    return _apply_percent_discount(net, _loyalty_discount_percent(settings, status))


def _loyalty_discount_percent(settings: Settings, status: UserStatus) -> Decimal:
    loyalty = settings.loyalty
    if status is UserStatus.gold:
        return loyalty.gold_discount_percent
    if status is UserStatus.silver:
        return loyalty.silver_discount_percent
    if status is UserStatus.bronze:
        return loyalty.bronze_discount_percent
    return Decimal("0")


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
