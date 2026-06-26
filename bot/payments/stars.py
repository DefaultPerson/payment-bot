from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from bot.config import Settings

if TYPE_CHECKING:
    from bot.services.pricing import TariffQuote

STARS_CURRENCY = "XTR"
STARS_RECURRING_MONTH_SECONDS = 2_592_000
USD_CENTS = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class StarsInvoiceQuote:
    amount: int
    currency: str
    subscription_period: int | None
    gross_usd: Decimal
    fee_usd: Decimal
    net_usd: Decimal


def quote_stars_invoice(settings: Settings, quote: TariffQuote) -> StarsInvoiceQuote:
    spendable_share = Decimal("1") - (settings.stars.app_store_fee_percent / Decimal("100"))
    if quote.tariff.stars_amount_override is not None:
        stars = Decimal(quote.tariff.stars_amount_override)
    else:
        stars = (quote.net_usd / settings.stars.star_usd_rate / spendable_share).to_integral_value(
            rounding=ROUND_CEILING,
        )
    gross_usd = _money(Decimal(stars) * settings.stars.star_usd_rate)
    fee_usd = _money(gross_usd * (settings.stars.app_store_fee_percent / Decimal("100")))
    net_usd = _money(gross_usd - fee_usd)
    return StarsInvoiceQuote(
        amount=int(stars),
        currency=STARS_CURRENCY,
        subscription_period=stars_subscription_period(quote.tariff.duration_months),
        gross_usd=gross_usd,
        fee_usd=fee_usd,
        net_usd=net_usd,
    )


def stars_subscription_period(duration_months: int) -> int | None:
    if duration_months == 1:
        return STARS_RECURRING_MONTH_SECONDS
    return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(USD_CENTS, rounding=ROUND_HALF_UP)
