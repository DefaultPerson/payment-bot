from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Subscription, SubscriptionSource, User, UserStatus
from bot.services.subscriptions import calculate_extension, status_after_total_active_days


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
    return await _extend_subscription(
        session,
        user=user,
        days=days,
        source=SubscriptionSource.manual,
        last_invoice_id=None,
        now=now,
        update_paid_fields=False,
    )


async def extend_paid_subscription(
    session: AsyncSession,
    *,
    user: User,
    days: int,
    source: SubscriptionSource,
    last_invoice_id: int,
    now: datetime,
) -> Subscription:
    return await _extend_subscription(
        session,
        user=user,
        days=days,
        source=source,
        last_invoice_id=last_invoice_id,
        now=now,
        update_paid_fields=True,
    )


async def _extend_subscription(
    session: AsyncSession,
    *,
    user: User,
    days: int,
    source: SubscriptionSource,
    last_invoice_id: int | None,
    now: datetime,
    update_paid_fields: bool,
) -> Subscription:
    locked_user = await _lock_user_for_update(session, user)
    subscription = await _get_subscription_for_user_for_update(session, locked_user.id)
    current_expires_at = subscription.expires_at if subscription is not None else None
    new_expires_at = calculate_extension(now=now, current_expires_at=current_expires_at, days=days)

    if subscription is None:
        subscription = Subscription(
            user_id=locked_user.id,
            started_at=now,
            expires_at=new_expires_at,
            source=source,
            last_invoice_id=last_invoice_id if update_paid_fields else None,
            stars_subscription_active=False,
            created_at=now,
            updated_at=now,
        )
        session.add(subscription)
    else:
        subscription.expires_at = new_expires_at
        subscription.updated_at = now
        if update_paid_fields:
            subscription.source = source
            subscription.last_invoice_id = last_invoice_id

    locked_user.total_active_days = (locked_user.total_active_days or 0) + days
    locked_user.status = status_after_total_active_days(
        locked_user.status or UserStatus.basic,
        locked_user.total_active_days,
    )
    locked_user.updated_at = now

    await session.flush()
    return subscription


async def _lock_user_for_update(session: AsyncSession, user: User) -> User:
    if user.id is None:
        msg = "user must be persisted before extending a subscription"
        raise ValueError(msg)

    locked_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None:
        msg = f"user with id {user.id} was not found"
        raise LookupError(msg)
    return locked_user


async def _get_subscription_for_user_for_update(
    session: AsyncSession,
    user_id: int,
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id).with_for_update(),
    )
