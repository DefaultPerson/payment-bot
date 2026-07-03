from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from bot.config import Settings
from bot.core.i18n import JsonI18n


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
    def __init__(self, current: str | None = "AdminRefResetState:waiting_input") -> None:
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

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(self.session)


@dataclass(slots=True)
class FakeUserRecord:
    id: int
    tg_id: int
    lang_code: str = "en"


def test_parse_refreset_args_accepts_valid_command() -> None:
    from bot.handlers.admin.refreset import parse_refreset_args

    assert parse_refreset_args("/refreset 1001") == 1001
    assert parse_refreset_args("/refreset@HydraBot 1001") == 1001


@pytest.mark.parametrize(
    "text",
    ["/refreset", "/refreset bad", "/refreset 0", "/refreset 1001 15", "/grant 1"],
)
def test_parse_refreset_args_rejects_invalid_command(text: str) -> None:
    from bot.handlers.admin.refreset import parse_refreset_args

    with pytest.raises(ValueError):
        parse_refreset_args(text)


def test_admin_router_registers_refreset_fsm_handlers() -> None:
    import bot.handlers.admin.refreset as refreset_module
    from bot.filters.admin import IsAdmin
    from bot.handlers.admin import create_admin_router

    router = create_admin_router()
    starts = [
        h for h in router.callback_query.handlers if h.callback is refreset_module.refreset_start
    ]
    inputs = [h for h in router.message.handlers if h.callback is refreset_module.refreset_input]

    assert len(starts) == 1
    assert len(inputs) == 1
    assert any(isinstance(f.callback, IsAdmin) for f in inputs[0].filters)


async def test_refreset_input_zeros_balance(monkeypatch) -> None:
    import bot.handlers.admin.refreset as refreset_module

    calls: list[dict[str, Any]] = []

    async def fake_ensure(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=1001)

    async def fake_reset(session: FakeSession, **kwargs: Any) -> int:
        calls.append({"session": session, **kwargs})
        return 4

    monkeypatch.setattr(refreset_module, "ensure_minimal_user", fake_ensure)
    monkeypatch.setattr(refreset_module, "reset_referral_balance", fake_reset)

    message = FakeMessage(text="1001", from_user=FakeTelegramUser(500, "en"))
    state = FakeState()

    await refreset_module.refreset_input(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        state=state,
    )

    assert calls and calls[0]["referrer"] == FakeUserRecord(id=7, tg_id=1001)
    assert (
        message.answers[0]["text"]
        == "Referral balance for user 1001 was zeroed. Earnings settled: 4."
    )
    assert state.current is None


async def test_refreset_input_invalid_reprompts() -> None:
    import bot.handlers.admin.refreset as refreset_module

    message = FakeMessage(text="not-an-id", from_user=FakeTelegramUser(500, "en"))
    state = FakeState()

    await refreset_module.refreset_input(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        state=state,
    )

    assert (
        message.answers[0]["text"]
        == "Invalid input. Send only the user id (a positive integer)."
    )
    assert state.current == "AdminRefResetState:waiting_input"


async def test_refreset_input_handles_database_error(monkeypatch) -> None:
    import bot.handlers.admin.refreset as refreset_module

    async def fail_ensure(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("db unavailable")

    monkeypatch.setattr(refreset_module, "ensure_minimal_user", fail_ensure)

    message = FakeMessage(text="1001", from_user=FakeTelegramUser(500, "en"))
    state = FakeState()

    await refreset_module.refreset_input(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        state=state,
    )

    assert message.answers[0]["text"] == "Something went wrong. Please try again later."
    assert state.current is None
