"""Admin-analytics aggregations (read-only, cross-entity).

All revenue/count queries filter ``status == paid`` and a ``[start, end]`` window on the
relevant timestamp. ``start is None`` means "all time" (no lower bound). Aggregate results are
normalised through ``Decimal(str(...))`` so SQLite (test) floats and Postgres (prod) Decimals
behave identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Invoice, InvoiceProvider, InvoiceStatus, ReferralEarning, User
from bot.services.pricing import money


@dataclass(frozen=True, slots=True)
class RevenueTotals:
    gross: Decimal
    net: Decimal
    fee: Decimal
    paid_count: int


@dataclass(frozen=True, slots=True)
class ProviderRevenue:
    provider: InvoiceProvider
    gross: Decimal
    net: Decimal
    paid_count: int


@dataclass(frozen=True, slots=True)
class TopReferrer:
    referrer_id: int
    tg_id: int
    username: str | None
    earned_usd: Decimal
    referral_count: int


def _money(value: object) -> Decimal:
    return money(Decimal(str(value or 0)))


def _paid_window(start: datetime | None, end: datetime):
    clause = [Invoice.status == InvoiceStatus.paid, Invoice.paid_at <= end]
    if start is not None:
        clause.append(Invoice.paid_at >= start)
    return clause


async def get_revenue_totals(
    session: AsyncSession,
    *,
    start: datetime | None,
    end: datetime,
) -> RevenueTotals:
    stmt = select(
        func.coalesce(func.sum(Invoice.gross), 0),
        func.coalesce(func.sum(Invoice.net), 0),
        func.coalesce(func.sum(Invoice.fee), 0),
        func.count(Invoice.id),
    ).where(*_paid_window(start, end))
    gross, net, fee, paid_count = (await session.execute(stmt)).one()
    return RevenueTotals(
        gross=_money(gross),
        net=_money(net),
        fee=_money(fee),
        paid_count=int(paid_count or 0),
    )


async def get_revenue_by_provider(
    session: AsyncSession,
    *,
    start: datetime | None,
    end: datetime,
) -> list[ProviderRevenue]:
    stmt = (
        select(
            Invoice.provider,
            func.coalesce(func.sum(Invoice.gross), 0),
            func.coalesce(func.sum(Invoice.net), 0),
            func.count(Invoice.id),
        )
        .where(*_paid_window(start, end))
        .group_by(Invoice.provider)
        .order_by(func.coalesce(func.sum(Invoice.gross), 0).desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        ProviderRevenue(
            provider=provider,
            gross=_money(gross),
            net=_money(net),
            paid_count=int(paid_count or 0),
        )
        for provider, gross, net, paid_count in rows
    ]


async def count_new_users(
    session: AsyncSession,
    *,
    start: datetime | None,
    end: datetime,
) -> int:
    stmt = select(func.count(User.id)).where(User.created_at <= end)
    if start is not None:
        stmt = stmt.where(User.created_at >= start)
    return int(await session.scalar(stmt) or 0)


async def get_top_referrers(
    session: AsyncSession,
    *,
    start: datetime | None,
    end: datetime,
    limit: int = 5,
) -> list[TopReferrer]:
    earned = func.coalesce(func.sum(ReferralEarning.amount_usd), 0)
    stmt = (
        select(
            ReferralEarning.referrer_id,
            User.tg_id,
            User.username,
            earned,
            func.count(func.distinct(ReferralEarning.referee_id)),
        )
        .join(User, User.id == ReferralEarning.referrer_id)
        .where(ReferralEarning.created_at <= end)
        .group_by(ReferralEarning.referrer_id, User.tg_id, User.username)
        .order_by(earned.desc())
        .limit(limit)
    )
    if start is not None:
        stmt = stmt.where(ReferralEarning.created_at >= start)
    rows = (await session.execute(stmt)).all()
    return [
        TopReferrer(
            referrer_id=referrer_id,
            tg_id=tg_id,
            username=username,
            earned_usd=_money(earned_usd),
            referral_count=int(referral_count or 0),
        )
        for referrer_id, tg_id, username, earned_usd, referral_count in rows
    ]


async def get_active_user_counts(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[int, int]:
    """Return ``(dau, mau)`` — distinct users active in the last 1 / 30 days."""
    dau = await session.scalar(
        select(func.count(User.id)).where(User.last_active_at >= now - timedelta(days=1)),
    )
    mau = await session.scalar(
        select(func.count(User.id)).where(User.last_active_at >= now - timedelta(days=30)),
    )
    return int(dau or 0), int(mau or 0)
