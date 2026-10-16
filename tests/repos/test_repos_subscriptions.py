from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bot.config import Settings
from bot.models import Subscription, SubscriptionSource, User, UserStatus
from bot.repos.subscriptions import (
    count_active_subscriptions,
    extend_manual_subscription,
    extend_paid_subscription,
    get_subscription_for_user,
    start_trial_subscription,
)


def _two_plan_settings() -> Settings:
    # main opens only -1001 at $1/day; pro opens -1001 + -1002 (superset) at $2/day.
    return Settings(
        access={
            "protected_chats": [
                {"chat_id": -1001, "title": "Main"},
                {"chat_id": -1002, "title": "Pro"},
            ]
        },
        tariffs={
            "plans": [
                {"code": "main", "chat_ids": [-1001]},
                {"code": "pro", "chat_ids": [-1001, -1002]},
            ],
            "items": [
                {"code": "main_1m", "plan_code": "main", "duration_months": 1,
                 "base_price_usd": "30"},
                {"code": "pro_1m", "plan_code": "pro", "duration_months": 1,
                 "base_price_usd": "60"},
            ],
        },
    )


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

        if entity is Subscription and field == "expires_at":
            return sum(1 for subscription in self.subscriptions if subscription.expires_at > value)

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
        referral_percent=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_subscription(
    *,
    subscription_id: int | None = None,
    user_id: int,
    started_at: datetime,
    expires_at: datetime,
    notified_3d_at: datetime | None = None,
    notified_0d_at: datetime | None = None,
    notified_post_at: datetime | None = None,
    access_revoked_at: datetime | None = None,
) -> Subscription:
    return Subscription(
        id=subscription_id,
        user_id=user_id,
        started_at=started_at,
        expires_at=expires_at,
        last_invoice_id=None,
        stars_subscription_active=False,
        source=SubscriptionSource.manual,
        notified_3d_at=notified_3d_at,
        notified_0d_at=notified_0d_at,
        notified_post_at=notified_post_at,
        access_revoked_at=access_revoked_at,
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
    assert user.status is UserStatus.basic
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


async def test_extend_subscription_resets_future_expiry_notification_markers() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=7, status=UserStatus.silver, total_active_days=20)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    subscription = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=20),
        expires_at=now + timedelta(days=3),
        notified_3d_at=now - timedelta(hours=1),
    )
    session.add(user)
    session.add(subscription)

    updated = await extend_manual_subscription(session, user=user, days=7, now=now)

    assert updated is subscription
    assert updated.notified_3d_at is None
    assert updated.notified_0d_at is None
    assert updated.notified_post_at is None
    assert updated.access_revoked_at is None


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
    assert user.status is UserStatus.basic
    assert session.flushed == 1


async def test_extend_subscription_resets_expired_notification_and_revoke_markers() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=8, total_active_days=5)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    subscription = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=20),
        expires_at=now - timedelta(days=3),
        notified_3d_at=now - timedelta(days=7),
        notified_0d_at=now - timedelta(days=3),
        notified_post_at=now - timedelta(days=2),
        access_revoked_at=now - timedelta(days=2),
    )
    session.add(user)
    session.add(subscription)

    updated = await extend_paid_subscription(
        session,
        user=user,
        days=14,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=123,
        now=now,
    )

    assert updated is subscription
    assert updated.expires_at == now + timedelta(days=14)
    assert updated.notified_3d_at is None
    assert updated.notified_0d_at is None
    assert updated.notified_post_at is None
    assert updated.access_revoked_at is None


async def test_extend_manual_subscription_recalculates_loyalty_tier() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=9, total_active_days=80)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    session.add(user)

    await extend_manual_subscription(session, user=user, days=100, now=now)

    # 80 prior days were already lived (no remaining coverage); the 100 just granted are all
    # still ahead, so consumed = 180 - 100 = 80 -> bronze. The grant confers tenure only as
    # its days actually elapse, exactly like a purchase.
    assert user.total_active_days == 180
    assert user.status is UserStatus.bronze


async def test_extend_paid_subscription_sets_source_and_last_invoice() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=10)
    session.add(user)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    subscription = await extend_paid_subscription(
        session,
        user=user,
        days=30,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=99,
        now=now,
    )

    assert subscription.user_id == user.id
    assert subscription.started_at == now
    assert subscription.expires_at == now + timedelta(days=30)
    assert subscription.source is SubscriptionSource.cryptobot
    assert subscription.last_invoice_id == 99
    assert user.total_active_days == 30
    # A first 1m purchase grants no instant tier: consumed = 30 - 30 remaining = 0 -> basic.
    assert user.status is UserStatus.basic
    assert session.flushed == 1
    assert session.scalar_queries[:2] == [
        (User, "id", True),
        (Subscription, "user_id", True),
    ]


async def test_extend_paid_subscription_stores_plan_code() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=12)
    session.add(user)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    subscription = await extend_paid_subscription(
        session,
        user=user,
        days=30,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=1,
        now=now,
        plan_code="main",
    )

    assert subscription.plan_code == "main"


async def test_extend_subscription_preserves_plan_when_extension_omits_it() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=13)
    session.add(user)
    started = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    existing = make_subscription(
        subscription_id=3,
        user_id=13,
        started_at=started,
        expires_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )
    existing.plan_code = "main"
    session.add(existing)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    updated = await extend_manual_subscription(session, user=user, days=7, now=now)

    assert updated is existing
    assert updated.plan_code == "main"


async def test_extend_paid_subscription_extends_from_future_and_preserves_higher_status() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=11, status=UserStatus.gold, total_active_days=5)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    current_expires_at = now + timedelta(days=7)
    subscription = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=20),
        expires_at=current_expires_at,
    )
    session.add(user)
    session.add(subscription)

    updated = await extend_paid_subscription(
        session,
        user=user,
        days=30,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=101,
        now=now,
    )

    assert updated is subscription
    assert updated.expires_at == current_expires_at + timedelta(days=30)
    assert updated.source is SubscriptionSource.cryptobot
    assert updated.last_invoice_id == 101
    assert user.total_active_days == 35
    assert user.status is UserStatus.gold
    assert session.flushed == 1


async def test_paid_upgrade_to_pricier_plan_prorates_carried_days() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=20)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    existing = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=300),
    )
    existing.plan_code = "main"
    session.add(user)
    session.add(existing)

    updated = await extend_paid_subscription(
        session,
        user=user,
        days=30,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=1,
        now=now,
        plan_code="pro",
        settings=_two_plan_settings(),
    )

    # main $1/day, pro $2/day -> carry 300*(1/2)=150 + 30 purchased = 180 days, now on pro.
    assert updated.plan_code == "pro"
    assert updated.expires_at == now + timedelta(days=180)


async def test_paid_downgrade_carries_full_remaining() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=21)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    existing = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=100),
    )
    existing.plan_code = "pro"
    session.add(user)
    session.add(existing)

    updated = await extend_paid_subscription(
        session,
        user=user,
        days=30,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=2,
        now=now,
        plan_code="main",
        settings=_two_plan_settings(),
    )

    # pro -> main is neither broader access nor pricier -> full carry, no proration.
    assert updated.plan_code == "main"
    assert updated.expires_at == now + timedelta(days=130)


async def test_paid_same_plan_renewal_is_not_prorated() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=22)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    existing = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=300),
    )
    existing.plan_code = "pro"
    session.add(user)
    session.add(existing)

    updated = await extend_paid_subscription(
        session,
        user=user,
        days=30,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=3,
        now=now,
        plan_code="pro",
        settings=_two_plan_settings(),
    )

    # Same plan -> full stack (no proration branch).
    assert updated.expires_at == now + timedelta(days=330)


async def test_paid_plan_change_without_settings_is_not_prorated() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=23)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    existing = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=300),
    )
    existing.plan_code = "main"
    session.add(user)
    session.add(existing)

    updated = await extend_paid_subscription(
        session,
        user=user,
        days=30,
        source=SubscriptionSource.cryptobot,
        last_invoice_id=4,
        now=now,
        plan_code="pro",
    )

    # No settings passed -> proration disabled -> full stack (regression guard).
    assert updated.plan_code == "pro"
    assert updated.expires_at == now + timedelta(days=330)


async def test_count_active_subscriptions_counts_future_expirations_only() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    session.subscriptions.extend(
        [
            make_subscription(user_id=1, started_at=now, expires_at=now + timedelta(seconds=1)),
            make_subscription(user_id=2, started_at=now, expires_at=now),
            make_subscription(user_id=3, started_at=now, expires_at=now - timedelta(seconds=1)),
        ],
    )

    count = await count_active_subscriptions(session, now=now)

    assert count == 1


async def test_start_trial_subscription_creates_grant_and_marks_used() -> None:
    session = FakeAsyncSession()
    user = make_user()
    session.add(user)
    await session.flush()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    activation = await start_trial_subscription(
        session, user=user, days=3, plan_code="main", now=now
    )

    assert activation.status == "activated"
    assert activation.subscription is not None
    assert activation.subscription.source is SubscriptionSource.trial
    assert activation.subscription.plan_code == "main"
    assert activation.subscription.expires_at == now + timedelta(days=3)
    assert activation.subscription.last_invoice_id is None
    assert user.trial_used_at == now
    assert user.total_active_days == 3


async def test_start_trial_subscription_is_one_time() -> None:
    session = FakeAsyncSession()
    user = make_user()
    session.add(user)
    await session.flush()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    first = await start_trial_subscription(session, user=user, days=3, plan_code="main", now=now)
    assert first.status == "activated"

    later = now + timedelta(days=90)
    second = await start_trial_subscription(session, user=user, days=3, plan_code="main", now=later)

    assert second.status == "already_used"
    assert second.subscription is None
    # Still exactly one subscription, expiry unchanged from the first grant.
    assert len(session.subscriptions) == 1
    assert session.subscriptions[0].expires_at == now + timedelta(days=3)


async def test_start_trial_subscription_blocked_by_active_subscription() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=5)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    session.add(user)
    session.add(
        make_subscription(
            user_id=user.id,
            started_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=10),
        )
    )

    activation = await start_trial_subscription(
        session, user=user, days=3, plan_code="main", now=now
    )

    assert activation.status == "has_active"
    assert activation.subscription is None
    assert user.trial_used_at is None


async def test_start_trial_subscription_reuses_lapsed_row_as_trial() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=6, total_active_days=30)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    lapsed = make_subscription(
        user_id=user.id,
        started_at=now - timedelta(days=40),
        expires_at=now - timedelta(days=5),
    )
    lapsed.source = SubscriptionSource.cryptobot
    lapsed.last_invoice_id = 999
    session.add(user)
    session.add(lapsed)

    activation = await start_trial_subscription(
        session, user=user, days=3, plan_code="main", now=now
    )

    assert activation.status == "activated"
    assert activation.subscription is lapsed
    assert lapsed.source is SubscriptionSource.trial  # provenance overwritten
    assert lapsed.last_invoice_id is None
    assert lapsed.expires_at == now + timedelta(days=3)  # from now, not stacked
    assert user.trial_used_at == now
