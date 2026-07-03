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
    spent_usd: Decimal = Decimal("0.00")

    @property
    def available_usd(self) -> Decimal:
        """Balance the user can still spend or withdraw: unpaid earnings minus spends."""
        return self.unpaid_earned_usd - self.spent_usd


class InsufficientReferralBalance(Exception):
    """Raised when a referral-balance purchase exceeds the available balance."""

    def __init__(self, available: Decimal) -> None:
        self.available = available
        super().__init__(f"insufficient referral balance: available={available}")


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
    spent_usd = await session.scalar(
        select(User.referral_spent_usd).where(User.id == referrer_id),
    )
    return ReferralStats(
        referral_count=len(referrals),
        earned_usd=earned_usd,
        unpaid_earned_usd=unpaid_earned_usd,
        spent_usd=spent_usd or Decimal("0.00"),
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


async def spend_referral_balance(
    session: AsyncSession,
    *,
    user: User,
    amount: Decimal,
) -> Decimal:
    """Debit ``amount`` USD from ``user``'s referral balance; return the remaining balance.

    Locks the user row ``FOR UPDATE`` so concurrent taps can't double-spend, then checks the
    derived balance (Σ unpaid earnings − already spent). Raises ``InsufficientReferralBalance``
    (carrying the currently available amount) when the balance is short.
    """
    if user.id is None:
        msg = "user must be persisted to spend referral balance"
        raise ValueError(msg)

    locked = await session.scalar(
        select(User).where(User.id == user.id).with_for_update(),
    )
    if locked is None:
        msg = f"user {user.id} was not found"
        raise LookupError(msg)

    earnings = await get_referral_earnings_for_referrer(session, user.id)
    unpaid = sum(
        (earning.amount_usd for earning in earnings if not earning.paid_out),
        Decimal("0.00"),
    )
    spent = locked.referral_spent_usd or Decimal("0.00")
    available = unpaid - spent
    if available < amount:
        raise InsufficientReferralBalance(available)

    locked.referral_spent_usd = spent + amount
    await session.flush()
    return available - amount


async def reset_referral_balance(
    session: AsyncSession,
    *,
    referrer: User,
    now: datetime,
) -> int:
    """Zero a user's spendable/withdrawable referral balance; return earnings cleared.

    Marks all outstanding earnings ``paid_out`` and resets ``referral_spent_usd`` to 0, so the
    derived available balance (Σ unpaid − spent) becomes 0. Lifetime ``earned_usd`` (sum of ALL
    earnings) is preserved — this settles the balance, it does not erase history.
    """
    if referrer.id is None:
        msg = "referrer must be persisted to reset referral balance"
        raise ValueError(msg)

    locked = await session.scalar(
        select(User).where(User.id == referrer.id).with_for_update(),
    )
    if locked is None:
        msg = f"user {referrer.id} was not found"
        raise LookupError(msg)

    earnings = await get_referral_earnings_for_referrer(session, referrer.id)
    cleared = 0
    for earning in earnings:
        if not earning.paid_out:
            earning.paid_out = True
            earning.paid_out_at = now
            cleared += 1
    locked.referral_spent_usd = Decimal("0.00")
    await session.flush()
    return cleared
