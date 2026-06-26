"""Assemble the admin stats snapshot from the read-only aggregations in ``bot.repos.stats``."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from bot.repos.stats import (
    ProviderRevenue,
    RevenueTotals,
    TopReferrer,
    count_new_users,
    get_active_user_counts,
    get_revenue_by_provider,
    get_revenue_totals,
    get_top_referrers,
)
from bot.repos.subscriptions import count_active_subscriptions


class StatsPeriod(str, enum.Enum):  # noqa: UP042
    today = "today"
    d7 = "7d"
    d30 = "30d"
    all = "all"


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    period: StatsPeriod
    revenue: RevenueTotals
    by_provider: list[ProviderRevenue]
    new_users: int
    active_subscriptions: int
    top_referrers: list[TopReferrer]
    dau: int
    mau: int


def resolve_period_bounds(period: StatsPeriod, now: datetime) -> tuple[datetime | None, datetime]:
    if period is StatsPeriod.today:
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now
    if period is StatsPeriod.d7:
        return now - timedelta(days=7), now
    if period is StatsPeriod.d30:
        return now - timedelta(days=30), now
    return None, now


async def build_stats_snapshot(
    session: AsyncSession,
    *,
    period: StatsPeriod,
    now: datetime,
) -> StatsSnapshot:
    start, end = resolve_period_bounds(period, now)
    revenue = await get_revenue_totals(session, start=start, end=end)
    by_provider = await get_revenue_by_provider(session, start=start, end=end)
    new_users = await count_new_users(session, start=start, end=end)
    active_subscriptions = await count_active_subscriptions(session, now=now)
    top_referrers = await get_top_referrers(session, start=start, end=end)
    dau, mau = await get_active_user_counts(session, now=now)
    return StatsSnapshot(
        period=period,
        revenue=revenue,
        by_provider=by_provider,
        new_users=new_users,
        active_subscriptions=active_subscriptions,
        top_referrers=top_referrers,
        dau=dau,
        mau=mau,
    )
