from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

import bot.jobs.notifications as notifications_module
from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.models import Subscription, SubscriptionSource, User, UserStatus

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_run_event_guard() -> None:
    # The run summary is gated to once per process; reset so each test starts fresh.
    notifications_module.reset_run_event_state()


class FakeBot:
    def __init__(self, *, fail_user_id: int | None = None, fail_revoke: bool = False) -> None:
        self.fail_user_id = fail_user_id
        self.fail_revoke = fail_revoke
        self.messages: list[dict[str, Any]] = []
        self.bans: list[dict[str, Any]] = []
        self.unbans: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        if kwargs["chat_id"] == self.fail_user_id:
            raise RuntimeError("telegram send failed")
        self.messages.append(kwargs)

    async def ban_chat_member(self, **kwargs: Any) -> None:
        if self.fail_revoke:
            raise RuntimeError("telegram ban failed")
        self.bans.append(kwargs)

    async def unban_chat_member(self, **kwargs: Any) -> None:
        self.unbans.append(kwargs)


class FakeServiceChannel:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.messages.append(text)
        return True


class FakeSession:
    def __init__(self, *, users: list[User], subscriptions: list[Subscription]) -> None:
        self.users = users
        self.subscriptions = subscriptions
        self.commits = 0
        self.rollbacks = 0
        self.flushed = 0

    async def scalar(self, statement: Any) -> User | None:
        user_id = statement.compile().params["id_1"]
        return next((user for user in self.users if user.id == user_id), None)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        self.flushed += 1


class FakeSessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(self.session)


@dataclass(slots=True)
class SelectorCalls:
    three_day: int = 0
    zero_day: int = 0
    post: int = 0
    revoke: int = 0


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


async def test_notify_subscriptions_once_sends_then_marks_notifications(monkeypatch) -> None:
    import bot.jobs.notifications as notifications_module

    user = make_user(user_id=1, tg_id=1001)
    three_day = make_subscription(user_id=1, expires_at=NOW + timedelta(days=3))
    zero_day = make_subscription(user_id=1, expires_at=NOW)
    post = make_subscription(user_id=1, expires_at=NOW - timedelta(hours=25))
    revoke = make_subscription(user_id=1, expires_at=NOW - timedelta(hours=25))
    calls = SelectorCalls()

    async def fake_3d(*args: Any, **kwargs: Any) -> list[Subscription]:
        calls.three_day += 1
        return [three_day]

    async def fake_0d(*args: Any, **kwargs: Any) -> list[Subscription]:
        calls.zero_day += 1
        return [zero_day]

    async def fake_post(*args: Any, **kwargs: Any) -> list[Subscription]:
        calls.post += 1
        return [post]

    async def fake_revoke(*args: Any, **kwargs: Any) -> list[Subscription]:
        calls.revoke += 1
        return [revoke]

    monkeypatch.setattr(notifications_module, "get_subscriptions_due_3d_notification", fake_3d)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_0d_notification", fake_0d)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_post_notification", fake_post)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_access_revoke", fake_revoke)

    bot = FakeBot()
    service_channel = FakeServiceChannel()
    result = await notifications_module.notify_subscriptions_once(
        session_factory=FakeSessionFactory(
            FakeSession(users=[user], subscriptions=[three_day, zero_day, post, revoke])
        ),
        settings=Settings(
            access={
                "revoke_ban_seconds": 60,
                "protected_chats": [{"chat_id": -1001, "title": "Signals"}],
            },
        ),
        bot=bot,
        i18n=JsonI18n(fallback_language="en"),
        service_channel=service_channel,
        now=NOW,
    )

    assert result.three_day_sent == 1
    assert result.zero_day_sent == 1
    assert result.post_sent == 1
    assert result.revoked == 1
    assert result.errors == 0
    assert three_day.notified_3d_at == NOW
    assert zero_day.notified_0d_at == NOW
    assert post.notified_post_at == NOW
    assert revoke.access_revoked_at == NOW
    assert len(bot.messages) == 3
    assert bot.bans[0]["until_date"] == NOW + timedelta(seconds=60)
    assert bot.unbans[0]["only_if_banned"] is True
    assert any("Subscription notification run" in text for text in service_channel.messages)


async def test_notify_subscriptions_once_retries_failed_user_send(monkeypatch) -> None:
    import bot.jobs.notifications as notifications_module

    user = make_user(user_id=1, tg_id=1001)
    subscription = make_subscription(user_id=1, expires_at=NOW + timedelta(days=3))

    async def fake_3d(*args: Any, **kwargs: Any) -> list[Subscription]:
        return [subscription]

    async def empty(*args: Any, **kwargs: Any) -> list[Subscription]:
        return []

    monkeypatch.setattr(notifications_module, "get_subscriptions_due_3d_notification", fake_3d)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_0d_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_post_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_access_revoke", empty)

    result = await notifications_module.notify_subscriptions_once(
        session_factory=FakeSessionFactory(FakeSession(users=[user], subscriptions=[subscription])),
        settings=Settings(),
        bot=FakeBot(fail_user_id=1001),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel(),
        now=NOW,
    )

    assert result.errors == 1
    assert result.three_day_sent == 0
    assert subscription.notified_3d_at is None


async def test_notify_subscriptions_once_does_not_mark_failed_revoke(monkeypatch) -> None:
    import bot.jobs.notifications as notifications_module

    user = make_user(user_id=1, tg_id=1001)
    subscription = make_subscription(user_id=1, expires_at=NOW - timedelta(hours=25))

    async def empty(*args: Any, **kwargs: Any) -> list[Subscription]:
        return []

    async def fake_revoke(*args: Any, **kwargs: Any) -> list[Subscription]:
        return [subscription]

    monkeypatch.setattr(notifications_module, "get_subscriptions_due_3d_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_0d_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_post_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_access_revoke", fake_revoke)

    result = await notifications_module.notify_subscriptions_once(
        session_factory=FakeSessionFactory(FakeSession(users=[user], subscriptions=[subscription])),
        settings=Settings(access={"protected_chats": [{"chat_id": -1001, "title": "Signals"}]}),
        bot=FakeBot(fail_revoke=True),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel(),
        now=NOW,
    )

    assert result.errors == 1
    assert result.revoked == 0
    assert subscription.access_revoked_at is None


async def test_revoke_emits_access_revoked_service_event(monkeypatch) -> None:
    import bot.jobs.notifications as notifications_module

    user = make_user(user_id=1, tg_id=1001)
    subscription = make_subscription(user_id=1, expires_at=NOW - timedelta(hours=25))

    async def empty(*args: Any, **kwargs: Any) -> list[Subscription]:
        return []

    async def fake_revoke(*args: Any, **kwargs: Any) -> list[Subscription]:
        return [subscription]

    monkeypatch.setattr(notifications_module, "get_subscriptions_due_3d_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_0d_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_post_notification", empty)
    monkeypatch.setattr(notifications_module, "get_subscriptions_due_access_revoke", fake_revoke)

    service_channel = FakeServiceChannel()
    result = await notifications_module.notify_subscriptions_once(
        session_factory=FakeSessionFactory(FakeSession(users=[user], subscriptions=[subscription])),
        settings=Settings(access={"protected_chats": [{"chat_id": -1001, "title": "Signals"}]}),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=service_channel,
        now=NOW,
    )

    assert result.revoked == 1
    revoke_events = [m for m in service_channel.messages if "Subscription access revoked" in m]
    assert len(revoke_events) == 1
    event = revoke_events[0]
    assert "<code>1001</code>" in event
    assert "subscription expired" in event
    assert "expired_at=" in event


async def test_notify_subscriptions_once_rolls_back_on_database_error(monkeypatch) -> None:
    import bot.jobs.notifications as notifications_module

    async def fail_selector(*args: Any, **kwargs: Any) -> list[Subscription]:
        raise SQLAlchemyError("db unavailable")

    monkeypatch.setattr(
        notifications_module,
        "get_subscriptions_due_3d_notification",
        fail_selector,
    )

    session = FakeSession(users=[], subscriptions=[])
    result = await notifications_module.notify_subscriptions_once(
        session_factory=FakeSessionFactory(session),
        settings=Settings(),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel(),
        now=NOW,
    )

    assert result.errors == 1
    assert session.rollbacks == 1


async def test_notification_loop_continues_after_unexpected_iteration_error(
    monkeypatch,
) -> None:
    import bot.jobs.notifications as notifications_module

    calls = 0
    sleeps: list[int] = []

    async def fake_notify_once(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected failure")

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        notifications_module,
        "notify_subscriptions_once",
        fake_notify_once,
    )

    await notifications_module.notification_loop(
        session_factory=FakeSessionFactory(FakeSession(users=[], subscriptions=[])),
        settings=Settings(notifications={"loop_interval_seconds": 5}),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel(),
        sleep=fake_sleep,
        max_iterations=2,
    )

    assert calls == 2
    assert sleeps == [5]


async def test_run_summary_posts_only_once_per_start() -> None:
    service_channel = FakeServiceChannel()
    result = notifications_module.NotificationRunResult(three_day_sent=1)

    await notifications_module._notify_service_channel(service_channel, result)
    await notifications_module._notify_service_channel(service_channel, result)

    assert len(service_channel.messages) == 1
    assert "Subscription notification run" in service_channel.messages[0]
    assert "first run since start" in service_channel.messages[0]


def test_notification_run_result_is_empty() -> None:
    assert notifications_module.NotificationRunResult().is_empty()
    assert not notifications_module.NotificationRunResult(errors=1).is_empty()
    assert not notifications_module.NotificationRunResult(three_day_sent=1).is_empty()
    assert not notifications_module.NotificationRunResult(revoked=1).is_empty()


async def test_run_summary_skips_when_all_zero() -> None:
    service_channel = FakeServiceChannel()

    await notifications_module._notify_service_channel(
        service_channel, notifications_module.NotificationRunResult()
    )

    assert service_channel.messages == []

    # an empty first run must not consume the once-per-start slot
    await notifications_module._notify_service_channel(
        service_channel, notifications_module.NotificationRunResult(revoked=1)
    )

    assert len(service_channel.messages) == 1
    assert "Subscription notification run" in service_channel.messages[0]
