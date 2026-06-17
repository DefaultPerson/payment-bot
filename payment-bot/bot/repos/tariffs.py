from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, TariffConfig
from bot.models import Tariff


def get_active_tariffs(settings: Settings) -> list[TariffConfig]:
    return [tariff for tariff in settings.tariffs.items if tariff.active]


def get_tariff(settings: Settings, code: str) -> TariffConfig:
    for tariff in get_active_tariffs(settings):
        if tariff.code == code:
            return tariff

    msg = f"tariff {code} was not found"
    raise LookupError(msg)


async def ensure_configured_tariff(
    session: AsyncSession,
    settings: Settings,
    code: str,
) -> Tariff:
    config = get_tariff(settings, code)
    tariff = await session.scalar(select(Tariff).where(Tariff.code == config.code))
    if tariff is None:
        tariff = Tariff(code=config.code)
        session.add(tariff)

    tariff.duration_months = config.duration_months
    tariff.base_price_usd = config.base_price_usd
    tariff.active = config.active
    await session.flush()
    return tariff
