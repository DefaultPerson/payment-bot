from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.models import Subscription, SubscriptionSource
from bot.repos.subscriptions import (
    get_subscriptions_due_0d_notification,
    get_subscriptions_due_3d_notification,
    get_subscriptions_due_access_revoke,
    get_subscriptions_due_post_notification,
    mark_subscription_0d_notified,
    mark_subscription_3d_notified,
    mark_subscription_access_revoked,
    mark_subscription_post_notified,
)

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


@dataclass(slots=True)
class FakeScalarResult:
    items: list[Subscription]

    def all(self) -> list[Subscription]:
        return self.items


class FakeAsyncSession:
    def __init__(self, subscriptions: list[Subscription]) -> None:
        self.subscriptions = subscriptions
        self.flushed = 0

    async def scalars(self, statement: Any) -> FakeScalarResult:
        where_sql = str(statement.whereclause)
        params = statement.compile().params
        limit = params.get("param_1")
        if "notified_3d_at IS NULL" in where_sql:
            start = _param(params, "expires_at_1")
            end = _param(params, "expires_at_2")
            rows = [
                subscription
                for subscription in self.subscriptions
                if subscription.notified_3d_at is None
                and start <= subscription.expires_at <= end
            ]
        elif "notified_0d_at IS NULL" in where_sql:
            deadline = _param(params, "expires_at_1")
            rows = [
                subscription
                for subscription in self.subscriptions
                if subscription.notified_0d_at is None and subscription.expires_at <= deadline
            ]
        elif "notified_post_at IS NULL" in where_sql:
            deadline = _param(params, "expires_at_1")
            rows = [
                subscription
                for subscription in self.subscriptions
                if subscription.notified_post_at is None and subscription.expires_at <= deadline
            ]
        elif "access_revoked_at IS NULL" in where_sql:
            deadline = _param(params, "expires_at_1")
            rows = [
                subscription
                for subscription in self.subscriptions
                if subscription.access_revoked_at is None and subscription.expires_at <= deadline
            ]
        else:
            msg = f"unsupported statement: {where_sql}"
            raise AssertionError(msg)

        rows.sort(key=lambda subscription: subscription.expires_at)
        return FakeScalarResult(rows[:limit] if limit is not None else rows)

    async def flush(self) -> None:
        self.flushed += 1


def make_subscription(
    *,
    user_id: int,
    expires_at: datetime,
    notified_3d_at: datetime | None = None,
    notified_0d_at: datetime | None = None,
    notified_post_at: datetime | None = None,
    access_revoked_at: datetime | None = None,
) -> Subscription:
    return Subscription(
        id=user_id,
        user_id=user_id,
        started_at=NOW - timedelta(days=30),
        expires_at=expires_at,
        last_invoice_id=None,
        stars_subscription_active=False,
        source=SubscriptionSource.manual,
        notified_3d_at=notified_3d_at,
        notified_0d_at=notified_0d_at,
        notified_post_at=notified_post_at,
        access_revoked_at=access_revoked_at,
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=30),
    )


async def test_get_subscriptions_due_3d_notification_uses_target_window() -> None:
    due = make_subscription(user_id=1, expires_at=NOW + timedelta(days=3, minutes=30))
    too_early = make_subscription(user_id=2, expires_at=NOW + timedelta(days=3, hours=2))
    already = make_subscription(
        user_id=3,
        expires_at=NOW + timedelta(days=3),
        notified_3d_at=NOW,
    )
    session = FakeAsyncSession([too_early, already, due])

    result = await get_subscriptions_due_3d_notification(
        session,
        now=NOW,
        days_before=3,
        window_seconds=3600,
        limit=10,
    )

    assert result == [due]


async def test_get_subscriptions_due_0d_post_and_revoke_notifications() -> None:
    expired = make_subscription(user_id=1, expires_at=NOW - timedelta(minutes=1))
    old_expired = make_subscription(user_id=2, expires_at=NOW - timedelta(hours=25))
    active = make_subscription(user_id=3, expires_at=NOW + timedelta(seconds=1))
    session = FakeAsyncSession([active, old_expired, expired])

    assert await get_subscriptions_due_0d_notification(session, now=NOW, limit=10) == [
        old_expired,
        expired,
    ]
    assert await get_subscriptions_due_post_notification(
        session,
        now=NOW,
        delay_hours=24,
        limit=10,
    ) == [old_expired]
    assert await get_subscriptions_due_access_revoke(
        session,
        now=NOW,
        delay_hours=24,
        limit=10,
    ) == [old_expired]


async def test_notification_mark_helpers_set_timestamps_and_flush() -> None:
    subscription = make_subscription(user_id=1, expires_at=NOW)
    session = FakeAsyncSession([subscription])

    await mark_subscription_3d_notified(session, subscription, notified_at=NOW)
    await mark_subscription_0d_notified(session, subscription, notified_at=NOW)
    await mark_subscription_post_notified(session, subscription, notified_at=NOW)
    await mark_subscription_access_revoked(session, subscription, revoked_at=NOW)

    assert subscription.notified_3d_at == NOW
    assert subscription.notified_0d_at == NOW
    assert subscription.notified_post_at == NOW
    assert subscription.access_revoked_at == NOW
    assert session.flushed == 4


def _param(params: dict[str, Any], key: str) -> datetime:
    value = params.get(key)
    if isinstance(value, datetime):
        return value
    for param_key, param_value in params.items():
        if param_key.startswith(key) and isinstance(param_value, datetime):
            return param_value
    msg = f"{key} not found in {params}"
    raise AssertionError(msg)
