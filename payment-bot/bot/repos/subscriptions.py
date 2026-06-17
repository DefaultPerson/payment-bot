from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Subscription, SubscriptionSource, User, UserStatus
from bot.services.subscriptions import calculate_extension, minimum_status_after_grant


async def get_subscription_for_user(
    session: AsyncSession,
    user_id: int,
) -> Subscription | None:
    return await session.scalar(select(Subscription).where(Subscription.user_id == user_id))


async def extend_manual_subscription(
    session: AsyncSession,
    *,
    user: User,
    days: int,
    now: datetime,
) -> Subscription:
    subscription = await get_subscription_for_user(session, user.id)
    current_expires_at = subscription.expires_at if subscription is not None else None
    new_expires_at = calculate_extension(now=now, current_expires_at=current_expires_at, days=days)

    if subscription is None:
        subscription = Subscription(
            user_id=user.id,
            started_at=now,
            expires_at=new_expires_at,
            source=SubscriptionSource.manual,
            stars_subscription_active=False,
            created_at=now,
            updated_at=now,
        )
        session.add(subscription)
    else:
        subscription.expires_at = new_expires_at
        subscription.updated_at = now

    user.total_active_days = (user.total_active_days or 0) + days
    user.status = minimum_status_after_grant(user.status or UserStatus.basic)
    user.updated_at = now

    await session.flush()
    return subscription
