from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from bot.config import Settings
from bot.core.i18n import JsonI18n


@dataclass(slots=True)
class FakeTelegramUser:
    id: int
    language_code: str | None = None


class FakeMessage:
    def __init__(
        self,
        *,
        text: str,
        from_user: FakeTelegramUser | None,
    ) -> None:
        self.text = text
        self.from_user = from_user
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeSessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.calls = 0

    def __call__(self) -> FakeSessionContext:
        self.calls += 1
        return FakeSessionContext(self.session)


@dataclass(slots=True)
class FakeUserRecord:
    id: int
    tg_id: int
    lang_code: str = "ru"


@dataclass(slots=True)
class FakeSubscription:
    expires_at: datetime


class FakeServiceChannel:
    def __init__(self, *, result: bool = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return self.result


async def test_is_admin_accepts_only_configured_admin_ids() -> None:
    from bot.filters.admin import IsAdmin

    settings = Settings(admin_ids=[100, 200])
    admin_message = FakeMessage(text="/grant 123 10", from_user=FakeTelegramUser(id=100))
    user_message = FakeMessage(text="/grant 123 10", from_user=FakeTelegramUser(id=300))
    anonymous_message = FakeMessage(text="/grant 123 10", from_user=None)

    admin_filter = IsAdmin()

    assert await admin_filter(admin_message, settings=settings) is True
    assert await admin_filter(user_message, settings=settings) is False
    assert await admin_filter(anonymous_message, settings=settings) is False


def test_admin_router_registers_filtered_grant_and_forbidden_fallback() -> None:
    import bot.handlers.admin.grant as grant_module
    from bot.filters.admin import IsAdmin
    from bot.handlers.admin import create_admin_router

    router = create_admin_router()
    handlers = router.message.handlers

    grant_handler_indexes = [
        index for index, handler in enumerate(handlers) if handler.callback is grant_module.grant
    ]
    fallback_handler_indexes = [
        index
        for index, handler in enumerate(handlers)
        if handler.callback is grant_module.grant_forbidden
    ]

    assert len(grant_handler_indexes) == 1
    assert len(fallback_handler_indexes) == 1
    assert grant_handler_indexes[0] < fallback_handler_indexes[0]

    grant_handler = handlers[grant_handler_indexes[0]]
    assert any(
        isinstance(filter_object.callback, IsAdmin) for filter_object in grant_handler.filters
    )


async def test_non_admin_grant_replies_forbidden_without_mutating(monkeypatch) -> None:
    import bot.handlers.admin.grant as grant_module

    async def fail_ensure(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("non-admin grant must not ensure users")

    async def fail_extend(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("non-admin grant must not extend subscriptions")

    monkeypatch.setattr(grant_module, "ensure_minimal_user", fail_ensure)
    monkeypatch.setattr(grant_module, "extend_manual_subscription", fail_extend)

    i18n = JsonI18n()
    message = FakeMessage(
        text="/grant 123 10",
        from_user=FakeTelegramUser(id=300, language_code="en"),
    )
    session_factory = FakeSessionFactory()
    service_channel = FakeServiceChannel()

    await grant_module.grant(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=i18n,
        session_factory=session_factory,
        service_channel=service_channel,
    )

    assert message.answers == [{"text": "Only admins can grant access."}]
    assert session_factory.calls == 0
    assert service_channel.calls == []


async def test_grant_forbidden_replies_forbidden_text() -> None:
    import bot.handlers.admin.grant as grant_module

    message = FakeMessage(
        text="/grant 123 10",
        from_user=FakeTelegramUser(id=300, language_code="en"),
    )

    await grant_module.grant_forbidden(message, i18n=JsonI18n())

    assert message.answers == [{"text": "Only admins can grant access."}]


@pytest.mark.parametrize(
    "text",
    [
        "/grant",
        "/grant 123",
        "/grant nope 10",
        "/grant 123 days",
        "/grant 123 10 extra",
        "/grant 0 10",
        "/grant -123 10",
        "/grant 123 0",
        "/grant 123 -1",
    ],
)
async def test_grant_with_missing_or_invalid_args_replies_usage(text: str) -> None:
    import bot.handlers.admin.grant as grant_module

    message = FakeMessage(
        text=text,
        from_user=FakeTelegramUser(id=100, language_code="en"),
    )
    session_factory = FakeSessionFactory()

    await grant_module.grant(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=JsonI18n(),
        session_factory=session_factory,
        service_channel=FakeServiceChannel(),
    )

    assert message.answers == [{"text": "Usage: /grant <telegram_id> <days>"}]
    assert session_factory.calls == 0


@pytest.mark.parametrize("text", ["/grant 123 10", "/grant@HydraBot 123 10"])
def test_parse_grant_args_accepts_command_and_bot_mention(text: str) -> None:
    from bot.handlers.admin.grant import parse_grant_args

    assert parse_grant_args(text) == (123, 10)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "/profile 123 10",
        "/grant",
        "/grant 123",
        "/grant 0 10",
        "/grant -123 10",
        "/grant 123 0",
        "/grant 123 -5",
        "/grant 123 nope",
        "/grant nope 10",
        "/grant 123 10 extra",
    ],
)
def test_parse_grant_args_rejects_invalid_input(text: str) -> None:
    from bot.handlers.admin.grant import parse_grant_args

    with pytest.raises(ValueError):
        parse_grant_args(text)


async def test_admin_grant_extends_subscription_notifies_service_channel_and_replies(
    monkeypatch,
) -> None:
    import bot.handlers.admin.grant as grant_module

    calls: list[dict[str, Any]] = []
    user = FakeUserRecord(id=7, tg_id=123, lang_code="en")
    expires_at = datetime(2026, 6, 27, 12, 30, tzinfo=UTC)
    subscription = FakeSubscription(expires_at=expires_at)

    async def fake_ensure_minimal_user(
        session: FakeSession,
        *,
        tg_id: int,
        i18n: JsonI18n,
    ) -> FakeUserRecord:
        calls.append({"name": "ensure", "session": session, "tg_id": tg_id, "i18n": i18n})
        return user

    async def fake_extend_manual_subscription(
        session: FakeSession,
        *,
        user: FakeUserRecord,
        days: int,
        now: datetime,
    ) -> FakeSubscription:
        calls.append({"name": "extend", "session": session, "user": user, "days": days, "now": now})
        return subscription

    monkeypatch.setattr(grant_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(grant_module, "extend_manual_subscription", fake_extend_manual_subscription)

    i18n = JsonI18n()
    session_factory = FakeSessionFactory()
    service_channel = FakeServiceChannel()
    message = FakeMessage(
        text="/grant 123 10",
        from_user=FakeTelegramUser(id=100, language_code="en"),
    )

    await grant_module.grant(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=i18n,
        session_factory=session_factory,
        service_channel=service_channel,
    )

    assert calls[0] == {
        "name": "ensure",
        "session": session_factory.session,
        "tg_id": 123,
        "i18n": i18n,
    }
    assert calls[1]["name"] == "extend"
    assert calls[1]["session"] is session_factory.session
    assert calls[1]["user"] is user
    assert calls[1]["days"] == 10
    assert calls[1]["now"].tzinfo is UTC
    assert session_factory.session.commits == 1

    assert len(service_channel.calls) == 1
    assert "manual grant" in service_channel.calls[0]
    assert "admin_id=100" in service_channel.calls[0]
    assert "target_tg_id=123" in service_channel.calls[0]
    assert message.answers == [
        {"text": "Granted access to user 123 for 10 days. Expires at 2026-06-27 12:30 UTC"}
    ]


@pytest.mark.parametrize(
    "service_channel",
    [
        FakeServiceChannel(result=False),
        FakeServiceChannel(error=RuntimeError("service channel failed")),
    ],
)
async def test_admin_grant_replies_when_service_channel_does_not_deliver(
    monkeypatch,
    service_channel: FakeServiceChannel,
) -> None:
    import bot.handlers.admin.grant as grant_module

    user = FakeUserRecord(id=7, tg_id=123, lang_code="en")
    subscription = FakeSubscription(expires_at=datetime(2026, 6, 27, 12, 30, tzinfo=UTC))

    async def fake_ensure_minimal_user(*args: Any, **kwargs: Any) -> FakeUserRecord:
        return user

    async def fake_extend_manual_subscription(*args: Any, **kwargs: Any) -> FakeSubscription:
        return subscription

    monkeypatch.setattr(grant_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(grant_module, "extend_manual_subscription", fake_extend_manual_subscription)

    message = FakeMessage(
        text="/grant 123 10",
        from_user=FakeTelegramUser(id=100, language_code="en"),
    )

    await grant_module.grant(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        service_channel=service_channel,
    )

    assert len(service_channel.calls) == 1
    assert message.answers == [
        {"text": "Granted access to user 123 for 10 days. Expires at 2026-06-27 12:30 UTC"}
    ]
