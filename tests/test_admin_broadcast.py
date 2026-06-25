from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram_broadcast import BroadcastResult
from sqlalchemy.exc import SQLAlchemyError

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.models import BroadcastLog, Subscription, SubscriptionSource, User, UserStatus

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


@dataclass(slots=True)
class FakeTelegramUser:
    id: int
    language_code: str | None = "en"


class FakeMessage:
    def __init__(self, *, text: str, from_user: FakeTelegramUser | None) -> None:
        self.text = text
        self.from_user = from_user
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


class FakeMetrics:
    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, int]] = []

    def record_broadcast(self, *, result: str, count: int = 1) -> None:
        self.broadcasts.append((result, count))


class FakeScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def all(self) -> list[Any]:
        return self.items


class FakeSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.subscriptions: list[Subscription] = []
        self.logs: list[BroadcastLog] = []
        self.events: list[str] = []
        self.flushed = 0
        self.commits = 0
        self.rollbacks = 0
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

    async def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def get(self, model: type[Any], ident: int) -> Any | None:
        if model is BroadcastLog:
            return next((log for log in self.logs if log.id == ident), None)
        msg = f"unsupported fake get for {model}"
        raise AssertionError(msg)

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


class FakeSessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(self.session)


@dataclass(slots=True)
class FakeAdminRecord:
    id: int
    tg_id: int
    lang_code: str = "en"


def make_user(*, user_id: int, tg_id: int) -> User:
    return User(
        id=user_id,
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code="en",
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


def test_parse_broadcast_args_accepts_segment_and_text() -> None:
    from bot.handlers.admin.broadcast import parse_broadcast_args

    assert parse_broadcast_args("/broadcast all Hello") == ("all", "Hello")
    assert parse_broadcast_args("/broadcast@HydraBot active Hello world") == (
        "active",
        "Hello world",
    )


@pytest.mark.parametrize(
    "text",
    ["/broadcast", "/broadcast all", "/broadcast vip Hello", "/grant all Hello"],
)
def test_parse_broadcast_args_rejects_invalid_command(text: str) -> None:
    from bot.handlers.admin.broadcast import parse_broadcast_args

    with pytest.raises(ValueError):
        parse_broadcast_args(text)


def test_admin_router_registers_broadcast_with_admin_filter() -> None:
    import bot.handlers.admin.broadcast as broadcast_module
    from bot.filters.admin import IsAdmin
    from bot.handlers.admin import create_admin_router

    handlers = create_admin_router().message.handlers
    indexes = [
        index
        for index, handler in enumerate(handlers)
        if handler.callback is broadcast_module.broadcast
    ]

    assert indexes
    assert any(
        isinstance(filter_object.callback, IsAdmin)
        for filter_object in handlers[indexes[0]].filters
    )


async def test_broadcast_forbidden_for_non_admin() -> None:
    from bot.handlers.admin.broadcast import broadcast

    message = FakeMessage(
        text="/broadcast all Hello",
        from_user=FakeTelegramUser(id=400, language_code="en"),
    )

    await broadcast(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=FakeBot(),
    )

    assert message.answers == [{"text": "Only admins can broadcast."}]


async def test_broadcast_sends_segment_logs_and_replies_to_admin(monkeypatch) -> None:
    import bot.handlers.admin.broadcast as broadcast_module

    async def fake_ensure_minimal_user(*args: Any, **kwargs: Any) -> FakeAdminRecord:
        return FakeAdminRecord(id=50, tg_id=500)

    async def fake_run_text_broadcast(*args: Any, **kwargs: Any) -> BroadcastResult:
        session.events.append("send")
        return BroadcastResult(total=2, successful=1, failed=1, errors={1002: "blocked"})

    monkeypatch.setattr(broadcast_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(broadcast_module, "run_text_broadcast", fake_run_text_broadcast)

    session = FakeSession()
    session.users.extend([make_user(user_id=1, tg_id=1001), make_user(user_id=2, tg_id=1002)])
    session.subscriptions.append(make_subscription(user_id=1, expires_at=NOW + timedelta(days=1)))
    message = FakeMessage(
        text="/broadcast active Hello users",
        from_user=FakeTelegramUser(id=500, language_code="en"),
    )
    metrics = FakeMetrics()

    await broadcast_module.broadcast(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(session),
        bot=FakeBot(),
        metrics=metrics,
    )

    assert message.answers == [{"text": "Broadcast finished for active: 1/2 sent, 1 failed."}]
    assert len(session.logs) == 1
    assert session.logs[0].admin_user_id == 50
    assert session.logs[0].target_filter == {"segment": "active"}
    assert session.logs[0].message_text == "Hello users"
    assert session.logs[0].total_count == 2
    assert session.logs[0].success_count == 1
    assert session.logs[0].failed_count == 1
    assert session.commits == 2
    assert session.events == ["commit", "send", "commit"]
    assert metrics.broadcasts == [("success", 1), ("failed", 1)]


async def test_broadcast_records_failed_metric_when_sender_fails(monkeypatch) -> None:
    import bot.handlers.admin.broadcast as broadcast_module

    async def fake_ensure_minimal_user(*args: Any, **kwargs: Any) -> FakeAdminRecord:
        return FakeAdminRecord(id=50, tg_id=500)

    async def fail_run_text_broadcast(*args: Any, **kwargs: Any) -> BroadcastResult:
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(broadcast_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(broadcast_module, "run_text_broadcast", fail_run_text_broadcast)

    session = FakeSession()
    session.users.extend([make_user(user_id=1, tg_id=1001), make_user(user_id=2, tg_id=1002)])
    message = FakeMessage(
        text="/broadcast all Plain text",
        from_user=FakeTelegramUser(id=500, language_code="en"),
    )
    metrics = FakeMetrics()

    await broadcast_module.broadcast(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(session),
        bot=FakeBot(),
        metrics=metrics,
    )

    assert message.answers == [{"text": "Something went wrong. Please try again later."}]
    assert metrics.broadcasts == [("failed", 2)]


async def test_broadcast_commits_started_log_before_sending(monkeypatch) -> None:
    import bot.handlers.admin.broadcast as broadcast_module

    async def fake_ensure_minimal_user(*args: Any, **kwargs: Any) -> FakeAdminRecord:
        return FakeAdminRecord(id=50, tg_id=500)

    async def fake_run_text_broadcast(*args: Any, **kwargs: Any) -> BroadcastResult:
        session.events.append("send")
        assert session.commits == 1
        assert session.logs[0].finished_at is None
        return BroadcastResult(total=2, successful=2, failed=0, errors={})

    monkeypatch.setattr(broadcast_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(broadcast_module, "run_text_broadcast", fake_run_text_broadcast)

    session = FakeSession()
    session.users.extend([make_user(user_id=1, tg_id=1001), make_user(user_id=2, tg_id=1002)])
    message = FakeMessage(
        text="/broadcast all Plain text",
        from_user=FakeTelegramUser(id=500, language_code="en"),
    )

    await broadcast_module.broadcast(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(session),
        bot=FakeBot(),
    )

    assert session.commits == 2
    assert session.events == ["commit", "send", "commit"]
    assert session.logs[0].finished_at is not None


async def test_broadcast_handles_database_error(monkeypatch) -> None:
    import bot.handlers.admin.broadcast as broadcast_module

    async def fail_ensure(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("db unavailable")

    monkeypatch.setattr(broadcast_module, "ensure_minimal_user", fail_ensure)

    message = FakeMessage(
        text="/broadcast all Hello",
        from_user=FakeTelegramUser(id=500, language_code="en"),
    )

    await broadcast_module.broadcast(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=FakeBot(),
    )

    assert message.answers == [{"text": "Something went wrong. Please try again later."}]
