from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.models import BroadcastLog, Subscription, SubscriptionSource, User, UserStatus
from bot.repos.broadcasts import (
    BroadcastTarget,
    create_broadcast_log,
    finish_broadcast_log,
    get_broadcast_targets,
)

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


@dataclass(slots=True)
class FakeScalarResult:
    items: list[Any]

    def all(self) -> list[Any]:
        return self.items


class FakeAsyncSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.subscriptions: list[Subscription] = []
        self.logs: list[BroadcastLog] = []
        self.flushed = 0
        self._next_log_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, BroadcastLog):
            self.logs.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for log in self.logs:
            if log.id is None:
                log.id = self._next_log_id
                self._next_log_id += 1

    async def scalars(self, statement: Any) -> FakeScalarResult:
        entity = statement.column_descriptions[0]["entity"]
        if entity is User:
            return FakeScalarResult(sorted(self.users, key=lambda user: user.id or 0))
        if entity is Subscription:
            return FakeScalarResult(
                [
                    subscription
                    for subscription in self.subscriptions
                    if subscription.expires_at > NOW
                ]
            )
        msg = f"unsupported fake scalars for {entity}"
        raise AssertionError(msg)


def make_user(*, user_id: int, tg_id: int, lang_code: str = "en") -> User:
    return User(
        id=user_id,
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code=lang_code,
        status=UserStatus.basic,
        total_active_days=0,
        referrer_id=None,
        referral_percent=None,
        pending_promo_id=None,
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=30),
    )


def make_subscription(*, user_id: int, expires_at: datetime) -> Subscription:
    return Subscription(
        id=user_id,
        user_id=user_id,
        started_at=NOW - timedelta(days=30),
        expires_at=expires_at,
        last_invoice_id=None,
        stars_subscription_active=False,
        source=SubscriptionSource.manual,
        notified_3d_at=None,
        notified_0d_at=None,
        notified_post_at=None,
        access_revoked_at=None,
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=30),
    )


async def test_get_broadcast_targets_segments_users_by_subscription_state() -> None:
    session = FakeAsyncSession()
    session.users.extend(
        [
            make_user(user_id=1, tg_id=1001),
            make_user(user_id=2, tg_id=1002, lang_code="ru"),
            make_user(user_id=3, tg_id=1003),
        ]
    )
    session.subscriptions.extend(
        [
            make_subscription(user_id=1, expires_at=NOW + timedelta(days=1)),
            make_subscription(user_id=2, expires_at=NOW - timedelta(seconds=1)),
        ]
    )

    assert await get_broadcast_targets(session, segment="all", now=NOW) == [
        BroadcastTarget(tg_id=1001, lang_code="en"),
        BroadcastTarget(tg_id=1002, lang_code="ru"),
        BroadcastTarget(tg_id=1003, lang_code="en"),
    ]
    assert await get_broadcast_targets(session, segment="active", now=NOW) == [
        BroadcastTarget(tg_id=1001, lang_code="en"),
    ]
    assert await get_broadcast_targets(session, segment="inactive", now=NOW) == [
        BroadcastTarget(tg_id=1002, lang_code="ru"),
        BroadcastTarget(tg_id=1003, lang_code="en"),
    ]


async def test_get_broadcast_targets_rejects_unknown_segment() -> None:
    session = FakeAsyncSession()

    try:
        await get_broadcast_targets(session, segment="vip", now=NOW)
    except ValueError as exc:
        assert "unknown broadcast segment" in str(exc)
    else:
        raise AssertionError("unknown segment must fail")


async def test_broadcast_log_start_and_finish_accounting() -> None:
    session = FakeAsyncSession()

    log = await create_broadcast_log(
        session,
        admin_user_id=100,
        message_text="hello",
        target_filter={"segment": "all"},
        started_at=NOW,
    )
    await finish_broadcast_log(
        session,
        log,
        total_count=3,
        success_count=2,
        failed_count=1,
        finished_at=NOW + timedelta(seconds=1),
    )

    assert log.id == 1
    assert log.admin_user_id == 100
    assert log.message_text == "hello"
    assert log.target_filter == {"segment": "all"}
    assert log.total_count == 3
    assert log.success_count == 2
    assert log.failed_count == 1
    assert log.finished_at == NOW + timedelta(seconds=1)
    assert session.flushed == 2
