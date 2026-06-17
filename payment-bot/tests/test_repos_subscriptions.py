from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bot.models import Subscription, SubscriptionSource, User, UserStatus
from bot.repos.subscriptions import extend_manual_subscription, get_subscription_for_user


class FakeAsyncSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.subscriptions: list[Subscription] = []
        self.flushed = 0
        self.scalar_queries: list[tuple[type, str, bool]] = []
        self._next_user_id = 1
        self._next_subscription_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, User) and instance not in self.users:
            self.users.append(instance)
        if isinstance(instance, Subscription) and instance not in self.subscriptions:
            self.subscriptions.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for user in self.users:
            if user.id is None:
                user.id = self._next_user_id
                self._next_user_id += 1
        for subscription in self.subscriptions:
            if subscription.id is None:
                subscription.id = self._next_subscription_id
                self._next_subscription_id += 1

    async def scalar(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        field = statement.whereclause.left.key
        value = statement.whereclause.right.value
        self.scalar_queries.append((entity, field, statement._for_update_arg is not None))

        if entity is User and field == "id":
            return next((user for user in self.users if user.id == value), None)

        if entity is Subscription and field == "user_id":
            return next(
                (
                    subscription
                    for subscription in self.subscriptions
                    if subscription.user_id == value
                ),
                None,
            )

        msg = f"unsupported fake query for {entity}.{field}"
        raise AssertionError(msg)


def make_user(
    *,
    user_id: int | None = None,
    tg_id: int = 1001,
    status: UserStatus = UserStatus.basic,
    total_active_days: int = 0,
) -> User:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return User(
        id=user_id,
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code="ru",
        status=status,
        total_active_days=total_active_days,
        referrer_id=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_subscription(
    *,
    subscription_id: int | None = None,
    user_id: int,
    started_at: datetime,
    expires_at: datetime,
) -> Subscription:
    return Subscription(
        id=subscription_id,
        user_id=user_id,
        started_at=started_at,
        expires_at=expires_at,
        last_invoice_id=None,
        stars_subscription_active=False,
        source=SubscriptionSource.manual,
        notified_3d_at=None,
        notified_0d_at=None,
        notified_post_at=None,
        access_revoked_at=None,
        created_at=started_at,
        updated_at=started_at,
    )


async def test_get_subscription_for_user_returns_none_when_absent() -> None:
    session = FakeAsyncSession()

    assert await get_subscription_for_user(session, 123) is None


async def test_extend_manual_subscription_creates_missing_subscription() -> None:
    session = FakeAsyncSession()
    user = make_user()
    session.add(user)
    await session.flush()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    subscription = await extend_manual_subscription(session, user=user, days=10, now=now)

    assert subscription.id == 1
    assert subscription.user_id == user.id
    assert subscription.started_at == now
    assert subscription.expires_at == now + timedelta(days=10)
    assert subscription.source is SubscriptionSource.manual
    assert user.total_active_days == 10
    assert user.status is UserStatus.bronze
    assert session.flushed == 2
    assert session.scalar_queries[:2] == [
        (User, "id", True),
        (Subscription, "user_id", True),
    ]


async def test_extend_manual_subscription_extends_from_future_expiration() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=7, status=UserStatus.silver, total_active_days=20)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    started_at = now - timedelta(days=5)
    current_expires_at = now + timedelta(days=3)
    subscription = make_subscription(
        user_id=user.id,
        started_at=started_at,
        expires_at=current_expires_at,
    )
    session.add(user)
    session.add(subscription)

    updated = await extend_manual_subscription(session, user=user, days=7, now=now)

    assert updated is subscription
    assert updated.started_at == started_at
    assert updated.expires_at == current_expires_at + timedelta(days=7)
    assert user.total_active_days == 27
    assert user.status is UserStatus.silver
    assert session.flushed == 1


async def test_extend_manual_subscription_extends_expired_subscription_from_now() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=8, total_active_days=5)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    started_at = now - timedelta(days=20)
    subscription = make_subscription(
        user_id=user.id,
        started_at=started_at,
        expires_at=now - timedelta(days=3),
    )
    session.add(user)
    session.add(subscription)

    updated = await extend_manual_subscription(session, user=user, days=14, now=now)

    assert updated is subscription
    assert updated.started_at == started_at
    assert updated.expires_at == now + timedelta(days=14)
    assert user.total_active_days == 19
    assert user.status is UserStatus.bronze
    assert session.flushed == 1


async def test_extend_manual_subscription_recalculates_loyalty_tier() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=9, total_active_days=80)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    session.add(user)

    await extend_manual_subscription(session, user=user, days=100, now=now)

    assert user.total_active_days == 180
    assert user.status is UserStatus.gold
