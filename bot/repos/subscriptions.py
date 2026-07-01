from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Subscription, SubscriptionSource, User, UserStatus
from bot.services.subscriptions import (
    SupportsLoyaltyThresholds,
    calculate_extension,
    status_after_total_active_days_for_policy,
    subscription_active_at,
)

TrialStatus = Literal["activated", "already_used", "has_active"]


@dataclass(frozen=True, slots=True)
class TrialActivation:
    status: TrialStatus
    subscription: Subscription | None


async def get_subscription_for_user(
    session: AsyncSession,
    user_id: int,
) -> Subscription | None:
    return await session.scalar(select(Subscription).where(Subscription.user_id == user_id))


async def count_active_subscriptions(
    session: AsyncSession,
    *,
    now: datetime,
) -> int:
    count = await session.scalar(
        select(func.count(Subscription.id)).where(Subscription.expires_at > now),
    )
    return int(count or 0)


async def get_subscriptions_due_3d_notification(
    session: AsyncSession,
    *,
    now: datetime,
    days_before: int,
    window_seconds: int,
    limit: int,
) -> list[Subscription]:
    target = now + timedelta(days=days_before)
    window = timedelta(seconds=window_seconds)
    result = await session.scalars(
        select(Subscription)
        .where(
            Subscription.notified_3d_at.is_(None),
            Subscription.expires_at >= target - window,
            Subscription.expires_at <= target + window,
        )
        .order_by(Subscription.expires_at.asc())
        .limit(limit),
    )
    return list(result.all())


async def get_subscriptions_due_0d_notification(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
) -> list[Subscription]:
    result = await session.scalars(
        select(Subscription)
        .where(
            Subscription.notified_0d_at.is_(None),
            Subscription.expires_at <= now,
        )
        .order_by(Subscription.expires_at.asc())
        .limit(limit),
    )
    return list(result.all())


async def get_subscriptions_due_post_notification(
    session: AsyncSession,
    *,
    now: datetime,
    delay_hours: int,
    limit: int,
) -> list[Subscription]:
    deadline = now - timedelta(hours=delay_hours)
    result = await session.scalars(
        select(Subscription)
        .where(
            Subscription.notified_post_at.is_(None),
            Subscription.expires_at <= deadline,
        )
        .order_by(Subscription.expires_at.asc())
        .limit(limit),
    )
    return list(result.all())


async def get_subscriptions_due_access_revoke(
    session: AsyncSession,
    *,
    now: datetime,
    delay_hours: int,
    limit: int,
) -> list[Subscription]:
    deadline = now - timedelta(hours=delay_hours)
    result = await session.scalars(
        select(Subscription)
        .where(
            Subscription.access_revoked_at.is_(None),
            Subscription.expires_at <= deadline,
        )
        .order_by(Subscription.expires_at.asc())
        .limit(limit),
    )
    return list(result.all())


async def mark_subscription_3d_notified(
    session: AsyncSession,
    subscription: Subscription,
    *,
    notified_at: datetime,
) -> None:
    subscription.notified_3d_at = notified_at
    await session.flush()


async def mark_subscription_0d_notified(
    session: AsyncSession,
    subscription: Subscription,
    *,
    notified_at: datetime,
) -> None:
    subscription.notified_0d_at = notified_at
    await session.flush()


async def mark_subscription_post_notified(
    session: AsyncSession,
    subscription: Subscription,
    *,
    notified_at: datetime,
) -> None:
    subscription.notified_post_at = notified_at
    await session.flush()


async def mark_subscription_access_revoked(
    session: AsyncSession,
    subscription: Subscription,
    *,
    revoked_at: datetime,
) -> None:
    subscription.access_revoked_at = revoked_at
    await session.flush()


async def extend_manual_subscription(
    session: AsyncSession,
    *,
    user: User,
    days: int,
    now: datetime,
    loyalty: SupportsLoyaltyThresholds | None = None,
    plan_code: str | None = None,
) -> Subscription:
    return await _extend_subscription(
        session,
        user=user,
        days=days,
        bonus_days=0,
        source=SubscriptionSource.manual,
        last_invoice_id=None,
        now=now,
        update_paid_fields=False,
        loyalty=loyalty,
        plan_code=plan_code,
    )


async def extend_paid_subscription(
    session: AsyncSession,
    *,
    user: User,
    days: int,
    source: SubscriptionSource,
    last_invoice_id: int,
    now: datetime,
    bonus_days: int = 0,
    loyalty: SupportsLoyaltyThresholds | None = None,
    plan_code: str | None = None,
) -> Subscription:
    return await _extend_subscription(
        session,
        user=user,
        days=days,
        bonus_days=bonus_days,
        source=source,
        last_invoice_id=last_invoice_id,
        now=now,
        update_paid_fields=True,
        loyalty=loyalty,
        plan_code=plan_code,
    )


async def start_trial_subscription(
    session: AsyncSession,
    *,
    user: User,
    days: int,
    plan_code: str | None,
    now: datetime,
    loyalty: SupportsLoyaltyThresholds | None = None,
) -> TrialActivation:
    """Atomically start a one-time free trial, gated by ``User.trial_used_at``.

    Runs under the same ``FOR UPDATE`` user lock as the extension, so the "trial used" check
    and mark are race-free. Returns ``already_used`` if the user has ever started a trial, or
    ``has_active`` if they currently hold an active subscription; neither mutates state.
    """
    if days <= 0:
        msg = "trial days must be positive"
        raise ValueError(msg)

    locked_user = await _lock_user_for_update(session, user)
    if locked_user.trial_used_at is not None:
        return TrialActivation(status="already_used", subscription=None)

    subscription = await _get_subscription_for_user_for_update(session, locked_user.id)
    if subscription is not None and subscription_active_at(subscription.expires_at, now):
        return TrialActivation(status="has_active", subscription=None)

    granted = await _extend_subscription(
        session,
        user=locked_user,
        days=days,
        bonus_days=0,
        source=SubscriptionSource.trial,
        last_invoice_id=None,
        now=now,
        update_paid_fields=False,
        loyalty=loyalty,
        plan_code=plan_code,
    )
    # Stamp trial provenance even when reusing a lapsed subscription row (whose source/invoice
    # link _extend_subscription leaves untouched under update_paid_fields=False).
    granted.source = SubscriptionSource.trial
    granted.last_invoice_id = None
    locked_user.trial_used_at = now
    await session.flush()
    return TrialActivation(status="activated", subscription=granted)


async def _extend_subscription(
    session: AsyncSession,
    *,
    user: User,
    days: int,
    bonus_days: int,
    source: SubscriptionSource,
    last_invoice_id: int | None,
    now: datetime,
    update_paid_fields: bool,
    loyalty: SupportsLoyaltyThresholds | None,
    plan_code: str | None = None,
) -> Subscription:
    if bonus_days < 0:
        msg = "bonus_days must not be negative"
        raise ValueError(msg)

    locked_user = await _lock_user_for_update(session, user)
    subscription = await _get_subscription_for_user_for_update(session, locked_user.id)
    current_expires_at = subscription.expires_at if subscription is not None else None
    new_expires_at = calculate_extension(
        now=now,
        current_expires_at=current_expires_at,
        days=days + bonus_days,
    )

    if subscription is None:
        subscription = Subscription(
            user_id=locked_user.id,
            started_at=now,
            expires_at=new_expires_at,
            source=source,
            last_invoice_id=last_invoice_id if update_paid_fields else None,
            plan_code=plan_code,
            stars_subscription_active=False,
            created_at=now,
            updated_at=now,
        )
        session.add(subscription)
    else:
        subscription.expires_at = new_expires_at
        _reset_notification_markers(subscription)
        subscription.updated_at = now
        # Only overwrite the stored plan when a caller supplies one, so a manual
        # extension that omits it doesn't wipe a paid subscription's plan.
        if plan_code is not None:
            subscription.plan_code = plan_code
        if update_paid_fields:
            subscription.source = source
            subscription.last_invoice_id = last_invoice_id

    locked_user.total_active_days = (locked_user.total_active_days or 0) + days
    locked_user.status = status_after_total_active_days_for_policy(
        locked_user.status or UserStatus.basic,
        locked_user.total_active_days,
        loyalty,
    )
    locked_user.updated_at = now

    await session.flush()
    return subscription


def _reset_notification_markers(subscription: Subscription) -> None:
    subscription.notified_3d_at = None
    subscription.notified_0d_at = None
    subscription.notified_post_at = None
    subscription.access_revoked_at = None


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
