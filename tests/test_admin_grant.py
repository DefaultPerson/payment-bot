from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from bot.config import Settings
from bot.core.i18n import JsonI18n

GRANT_SUCCESS = "Granted access to user 123 for 10 days. Expires at 2026-06-27 12:30 UTC."


@dataclass(slots=True)
class FakeTelegramUser:
    id: int
    language_code: str | None = None


class FakeMessage:
    def __init__(self, *, text: str, from_user: FakeTelegramUser | None) -> None:
        self.text = text
        self.from_user = from_user
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


class FakeState:
    def __init__(self, current: str | None = "AdminGrantState:waiting_input") -> None:
        self.current = current

    async def set_state(self, value: Any) -> None:
        self.current = value


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
    username: str | None = None


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


def _patch_grant(monkeypatch, *, user: FakeUserRecord, subscription: FakeSubscription) -> None:
    import bot.handlers.admin.grant as grant_module

    async def fake_ensure(session: Any, *, tg_id: int, i18n: JsonI18n) -> FakeUserRecord:
        return user

    async def fake_extend(
        session: Any, *, user: FakeUserRecord, days: int, now, loyalty, plan_code=None
    ):
        return subscription

    monkeypatch.setattr(grant_module, "ensure_minimal_user", fake_ensure)
    monkeypatch.setattr(grant_module, "extend_manual_subscription", fake_extend)


async def test_is_admin_accepts_only_configured_admin_ids() -> None:
    from bot.filters.admin import IsAdmin

    settings = Settings(admin_ids=[100, 200])
    admin_filter = IsAdmin()

    assert (
        await admin_filter(
            FakeMessage(text="x", from_user=FakeTelegramUser(100)), settings=settings
        )
        is True
    )
    assert (
        await admin_filter(
            FakeMessage(text="x", from_user=FakeTelegramUser(300)), settings=settings
        )
        is False
    )
    assert await admin_filter(FakeMessage(text="x", from_user=None), settings=settings) is False


def test_admin_router_registers_grant_fsm_handlers() -> None:
    import bot.handlers.admin.grant as grant_module
    from bot.filters.admin import IsAdmin
    from bot.handlers.admin import create_admin_router

    router = create_admin_router()
    starts = [h for h in router.callback_query.handlers if h.callback is grant_module.grant_start]
    inputs = [h for h in router.message.handlers if h.callback is grant_module.grant_input]

    assert len(starts) == 1
    assert len(inputs) == 1
    assert any(isinstance(f.callback, IsAdmin) for f in starts[0].filters)
    assert any(isinstance(f.callback, IsAdmin) for f in inputs[0].filters)


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


async def test_grant_input_extends_subscription_notifies_and_replies(monkeypatch) -> None:
    import bot.handlers.admin.grant as grant_module

    user = FakeUserRecord(id=7, tg_id=123, lang_code="en", username="bob")
    subscription = FakeSubscription(expires_at=datetime(2026, 6, 27, 12, 30, tzinfo=UTC))
    _patch_grant(monkeypatch, user=user, subscription=subscription)

    message = FakeMessage(text="123 10", from_user=FakeTelegramUser(100, "en"))
    session_factory = FakeSessionFactory()
    service_channel = FakeServiceChannel()
    state = FakeState()

    await grant_module.grant_input(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=JsonI18n(),
        session_factory=session_factory,
        service_channel=service_channel,
        state=state,
    )

    assert session_factory.session.commits == 1
    assert len(service_channel.calls) == 1
    assert "Admin manual grant" in service_channel.calls[0]
    assert "<code>123</code>" in service_channel.calls[0]
    assert message.answers[0]["text"] == GRANT_SUCCESS
    assert state.current is None


async def test_grant_preserves_current_plan(monkeypatch) -> None:
    import bot.handlers.admin.grant as grant_module

    captured: dict[str, Any] = {}
    user = FakeUserRecord(id=7, tg_id=123, lang_code="en")

    async def fake_ensure(session: Any, *, tg_id: int, i18n: JsonI18n) -> FakeUserRecord:
        return user

    async def fake_extend(session: Any, *, user, days, now, loyalty, plan_code=None):
        captured["plan_code"] = plan_code
        return FakeSubscription(expires_at=datetime(2026, 6, 27, 12, 30, tzinfo=UTC))

    monkeypatch.setattr(grant_module, "ensure_minimal_user", fake_ensure)
    monkeypatch.setattr(grant_module, "extend_manual_subscription", fake_extend)

    await grant_module.grant_input(
        FakeMessage(text="123 10", from_user=FakeTelegramUser(100, "en")),
        settings=Settings(admin_ids=[100]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        service_channel=FakeServiceChannel(),
        state=FakeState(),
    )

    # Grant must NOT force a plan (None => _extend_subscription preserves the user's current
    # plan instead of silently downgrading a PRO user to the default plan).
    assert captured["plan_code"] is None


async def test_grant_input_invalid_args_reprompts_without_db(monkeypatch) -> None:
    import bot.handlers.admin.grant as grant_module

    message = FakeMessage(text="nope", from_user=FakeTelegramUser(100, "en"))
    session_factory = FakeSessionFactory()
    state = FakeState()

    await grant_module.grant_input(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=JsonI18n(),
        session_factory=session_factory,
        service_channel=FakeServiceChannel(),
        state=state,
    )

    assert (
        message.answers[0]["text"] == "Invalid input. Send: user_id days (both positive integers)."
    )
    assert session_factory.calls == 0
    assert state.current == "AdminGrantState:waiting_input"


async def test_grant_input_service_channel_failure_still_replies(monkeypatch) -> None:
    import bot.handlers.admin.grant as grant_module

    user = FakeUserRecord(id=7, tg_id=123, lang_code="en")
    subscription = FakeSubscription(expires_at=datetime(2026, 6, 27, 12, 30, tzinfo=UTC))
    _patch_grant(monkeypatch, user=user, subscription=subscription)

    message = FakeMessage(text="123 10", from_user=FakeTelegramUser(100, "en"))
    state = FakeState()

    await grant_module.grant_input(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        service_channel=FakeServiceChannel(error=RuntimeError("down")),
        state=state,
    )

    assert message.answers[0]["text"] == GRANT_SUCCESS
    assert state.current is None


async def test_grant_input_surfaces_database_errors(monkeypatch) -> None:
    import bot.handlers.admin.grant as grant_module

    async def fail_ensure(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(grant_module, "ensure_minimal_user", fail_ensure)

    message = FakeMessage(text="123 10", from_user=FakeTelegramUser(100, "en"))
    session_factory = FakeSessionFactory()
    service_channel = FakeServiceChannel()
    state = FakeState()

    await grant_module.grant_input(
        message,
        settings=Settings(admin_ids=[100]),
        i18n=JsonI18n(),
        session_factory=session_factory,
        service_channel=service_channel,
        state=state,
    )

    assert session_factory.session.rollbacks == 1
    assert service_channel.calls == []
    assert message.answers[0]["text"] == "Something went wrong. Please try again later."
    assert state.current is None
