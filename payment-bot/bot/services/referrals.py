from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import User
from bot.repos.referrals import (
    create_referral,
    create_referral_earning_once,
    get_referral_by_referee_id,
)
from bot.repos.users import get_user_by_tg_id

USD_CENTS = Decimal("0.01")


class ReferralBindStatus(str, enum.Enum):  # noqa: UP042
    applied = "applied"
    ignored = "ignored"
    already_bound = "already_bound"
    rebind_rejected = "rebind_rejected"


@dataclass(frozen=True, slots=True)
class ReferralBindResult:
    status: ReferralBindStatus
    referrer_tg_id: int | None = None


def parse_referral_payload(text: str | None) -> int | None:
    if not text:
        return None

    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/start":
        return None

    payload = parts[1]
    if not payload.startswith("ref_"):
        return None
    try:
        referrer_tg_id = int(payload.removeprefix("ref_"))
    except ValueError:
        return None
    return referrer_tg_id if referrer_tg_id > 0 else None


async def bind_referral_from_start(
    session: AsyncSession,
    *,
    referee: User,
    referrer_tg_id: int,
    now: datetime,
    allow_referrer_change: bool,
) -> ReferralBindResult:
    if referee.id is None:
        msg = "referee must be persisted before referral binding"
        raise ValueError(msg)
    if referee.tg_id == referrer_tg_id:
        return ReferralBindResult(status=ReferralBindStatus.ignored)

    referrer = await get_user_by_tg_id(session, referrer_tg_id)
    if referrer is None or referrer.id is None:
        return ReferralBindResult(status=ReferralBindStatus.ignored)
    if await _would_create_cycle(session, referrer=referrer, referee=referee):
        return ReferralBindResult(status=ReferralBindStatus.ignored)

    existing = await get_referral_by_referee_id(session, referee.id)
    current_referrer_id = existing.referrer_id if existing is not None else referee.referrer_id
    if current_referrer_id is not None:
        if current_referrer_id == referrer.id:
            return ReferralBindResult(
                status=ReferralBindStatus.already_bound,
                referrer_tg_id=referrer.tg_id,
            )
        if not allow_referrer_change:
            return ReferralBindResult(
                status=ReferralBindStatus.rebind_rejected,
                referrer_tg_id=referrer.tg_id,
            )
        if existing is not None:
            existing.referrer_id = referrer.id
            referee.referrer_id = referrer.id
            await session.flush()
            return ReferralBindResult(
                status=ReferralBindStatus.applied,
                referrer_tg_id=referrer.tg_id,
            )

    await create_referral(session, referrer=referrer, referee=referee, joined_at=now)
    return ReferralBindResult(status=ReferralBindStatus.applied, referrer_tg_id=referrer.tg_id)


async def record_referral_earning(
    session: AsyncSession,
    *,
    referral_id: int,
    referrer_id: int,
    referee_id: int,
    invoice_id: int,
    invoice_net_usd: Decimal,
    percent: Decimal,
    created_at: datetime,
):
    del referral_id
    amount_usd = money(invoice_net_usd * (percent / Decimal("100")))
    if amount_usd <= 0:
        return None
    return await create_referral_earning_once(
        session,
        referrer_id=referrer_id,
        referee_id=referee_id,
        invoice_id=invoice_id,
        amount_usd=amount_usd,
        created_at=created_at,
    )


def money(value: Decimal) -> Decimal:
    return value.quantize(USD_CENTS, rounding=ROUND_HALF_UP)


async def _would_create_cycle(
    session: AsyncSession,
    *,
    referrer: User,
    referee: User,
) -> bool:
    if referrer.id == referee.id:
        return True

    seen: set[int] = set()
    current = referrer
    while current.referrer_id is not None:
        if current.referrer_id == referee.id:
            return True
        if current.referrer_id in seen:
            return True
        seen.add(current.referrer_id)
        next_user = await session.scalar(select(User).where(User.id == current.referrer_id))
        if next_user is None:
            return False
        current = next_user
    return False
