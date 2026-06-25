from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Referral, ReferralEarning, User


@dataclass(frozen=True, slots=True)
class ReferralStats:
    referral_count: int
    earned_usd: Decimal
    unpaid_earned_usd: Decimal


async def get_referral_by_referee_id(
    session: AsyncSession,
    referee_id: int,
) -> Referral | None:
    return await session.scalar(select(Referral).where(Referral.referee_id == referee_id))


async def get_referrals_for_referrer(
    session: AsyncSession,
    referrer_id: int,
) -> list[Referral]:
    result = await session.scalars(
        select(Referral).where(Referral.referrer_id == referrer_id).order_by(Referral.id.asc()),
    )
    return list(result.all())


async def get_referral_earnings_for_referrer(
    session: AsyncSession,
    referrer_id: int,
) -> list[ReferralEarning]:
    result = await session.scalars(
        select(ReferralEarning)
        .where(ReferralEarning.referrer_id == referrer_id)
        .order_by(ReferralEarning.id.asc()),
    )
    return list(result.all())


async def get_referral_stats(
    session: AsyncSession,
    *,
    referrer_id: int,
) -> ReferralStats:
    referrals = await get_referrals_for_referrer(session, referrer_id)
    earnings = await get_referral_earnings_for_referrer(session, referrer_id)
    earned_usd = sum((earning.amount_usd for earning in earnings), Decimal("0.00"))
    unpaid_earned_usd = sum(
        (earning.amount_usd for earning in earnings if not earning.paid_out),
        Decimal("0.00"),
    )
    return ReferralStats(
        referral_count=len(referrals),
        earned_usd=earned_usd,
        unpaid_earned_usd=unpaid_earned_usd,
    )


async def create_referral(
    session: AsyncSession,
    *,
    referrer: User,
    referee: User,
    joined_at: datetime,
    custom_percent: Decimal | None = None,
) -> Referral:
    if referrer.id is None or referee.id is None:
        msg = "referrer and referee must be persisted users"
        raise ValueError(msg)

    referral = Referral(
        referrer_id=referrer.id,
        referee_id=referee.id,
        joined_at=joined_at,
        custom_percent=custom_percent if custom_percent is not None else referrer.referral_percent,
    )
    referee.referrer_id = referrer.id
    session.add(referral)
    await session.flush()
    return referral


async def get_referral_earning_by_invoice_id(
    session: AsyncSession,
    invoice_id: int,
) -> ReferralEarning | None:
    return await session.scalar(
        select(ReferralEarning).where(ReferralEarning.invoice_id == invoice_id),
    )


async def create_referral_earning_once(
    session: AsyncSession,
    *,
    referrer_id: int,
    referee_id: int,
    invoice_id: int,
    amount_usd: Decimal,
    created_at: datetime,
) -> ReferralEarning:
    existing = await get_referral_earning_by_invoice_id(session, invoice_id)
    if existing is not None:
        return existing

    statement = (
        insert(ReferralEarning)
        .values(
            referrer_id=referrer_id,
            referee_id=referee_id,
            invoice_id=invoice_id,
            amount_usd=amount_usd,
            created_at=created_at,
            paid_out=False,
            paid_out_at=None,
        )
        .on_conflict_do_nothing(index_elements=[ReferralEarning.invoice_id])
    )
    await session.execute(statement)
    earning = await get_referral_earning_by_invoice_id(session, invoice_id)
    if earning is None:
        msg = f"referral earning for invoice {invoice_id} was not found after insert"
        raise LookupError(msg)
    return earning


async def set_custom_percent_for_referrer(
    session: AsyncSession,
    *,
    referrer: User,
    percent: Decimal,
) -> int:
    if referrer.id is None:
        msg = "referrer must be persisted before setting custom percent"
        raise ValueError(msg)

    referrer.referral_percent = percent
    referrals = await get_referrals_for_referrer(session, referrer.id)
    for referral in referrals:
        referral.custom_percent = percent
    await session.flush()
    return len(referrals)
