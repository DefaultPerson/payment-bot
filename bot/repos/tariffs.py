from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import PlanConfig, Settings, TariffConfig
from bot.models import Tariff


def get_active_tariffs(settings: Settings) -> list[TariffConfig]:
    return [tariff for tariff in settings.tariffs.items if tariff.active]


def get_tariff(settings: Settings, code: str) -> TariffConfig:
    for tariff in get_active_tariffs(settings):
        if tariff.code == code:
            return tariff

    msg = f"tariff {code} was not found"
    raise LookupError(msg)


def get_active_plans(settings: Settings) -> list[PlanConfig]:
    return [plan for plan in settings.tariffs.plans if plan.active]


def get_plan(settings: Settings, code: str) -> PlanConfig:
    # Resolve from every configured plan (incl. inactive) so a subscription's stored
    # plan still resolves after the plan is retired.
    for plan in settings.tariffs.plans:
        if plan.code == code:
            return plan

    msg = f"plan {code} was not found"
    raise LookupError(msg)


def default_plan(settings: Settings) -> PlanConfig:
    return get_plan(settings, settings.tariffs.default_plan_code)


def get_plan_tariffs(settings: Settings, plan_code: str) -> list[TariffConfig]:
    return [tariff for tariff in get_active_tariffs(settings) if tariff.plan_code == plan_code]


def plan_for_tariff(settings: Settings, tariff_code: str) -> PlanConfig:
    for tariff in settings.tariffs.items:
        if tariff.code == tariff_code:
            return get_plan(settings, tariff.plan_code)

    msg = f"tariff {tariff_code} has no configured plan"
    raise LookupError(msg)


def resolve_plan_code(settings: Settings, tariff_code: str | None) -> str | None:
    """Best-effort plan code for a tariff code; ``None`` if it can't be resolved.

    Used to stamp a subscription's plan from a paid invoice without raising on
    legacy/retired tariff codes (those simply fall back to all-chats access).
    """
    if tariff_code is None:
        return None
    try:
        return plan_for_tariff(settings, tariff_code).code
    except LookupError:
        return None


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
