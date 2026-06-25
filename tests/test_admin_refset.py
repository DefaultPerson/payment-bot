from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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


def test_parse_refset_args_accepts_valid_command() -> None:
    from bot.handlers.admin.refset import parse_refset_args

    assert parse_refset_args("/refset 1001 15") == (1001, Decimal("15"))
    assert parse_refset_args("/refset@HydraBot 1001 12.5") == (1001, Decimal("12.5"))


@pytest.mark.parametrize(
    "text",
    ["/refset", "/refset bad 10", "/refset 1001 -1", "/refset 1001 100", "/grant 1 2"],
)
def test_parse_refset_args_rejects_invalid_command(text: str) -> None:
    from bot.handlers.admin.refset import parse_refset_args

    with pytest.raises(ValueError):
        parse_refset_args(text)


def test_admin_router_registers_refset_with_admin_filter() -> None:
    import bot.handlers.admin.refset as refset_module
    from bot.filters.admin import IsAdmin
    from bot.handlers.admin import create_admin_router

    handlers = create_admin_router().message.handlers
    indexes = [
        index for index, handler in enumerate(handlers) if handler.callback is refset_module.refset
    ]

    assert indexes
    handler = handlers[indexes[0]]
    assert any(isinstance(filter_object.callback, IsAdmin) for filter_object in handler.filters)


async def test_refset_updates_referrer_custom_percent(monkeypatch) -> None:
    import bot.handlers.admin.refset as refset_module

    calls: list[dict[str, Any]] = []

    async def fake_ensure(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=1001)

    async def fake_set_custom_percent(session: FakeSession, **kwargs: Any) -> int:
        calls.append({"session": session, **kwargs})
        return 3

    monkeypatch.setattr(refset_module, "ensure_minimal_user", fake_ensure)
    monkeypatch.setattr(refset_module, "set_custom_percent_for_referrer", fake_set_custom_percent)

    message = FakeMessage(
        text="/refset 1001 15",
        from_user=FakeTelegramUser(id=500, language_code="en"),
    )
    session_factory = FakeSessionFactory()

    await refset_module.refset(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=session_factory,
    )

    assert calls == [
        {
            "session": session_factory.session,
            "referrer": FakeUserRecord(id=7, tg_id=1001),
            "percent": Decimal("15"),
        }
    ]
    assert message.answers == [
        {"text": "Referral percent for user 1001 set to 15%. Updated links: 3."}
    ]


async def test_refset_reports_no_referrals_but_stores_future_override(monkeypatch) -> None:
    import bot.handlers.admin.refset as refset_module

    async def fake_ensure(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=1001)

    async def fake_set_custom_percent(session: FakeSession, **kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(refset_module, "ensure_minimal_user", fake_ensure)
    monkeypatch.setattr(refset_module, "set_custom_percent_for_referrer", fake_set_custom_percent)

    message = FakeMessage(
        text="/refset 1001 15",
        from_user=FakeTelegramUser(id=500, language_code="en"),
    )

    await refset_module.refset(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert message.answers == [
        {"text": "Referral percent for user 1001 set to 15%. Future referrals will use it."}
    ]


async def test_refset_forbidden_for_non_admin() -> None:
    from bot.handlers.admin.refset import refset

    message = FakeMessage(
        text="/refset 1001 15",
        from_user=FakeTelegramUser(id=400, language_code="en"),
    )

    await refset(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert message.answers == [{"text": "Only admins can set referral percent."}]


async def test_refset_handles_database_error(monkeypatch) -> None:
    import bot.handlers.admin.refset as refset_module

    async def fail_ensure(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("db unavailable")

    monkeypatch.setattr(refset_module, "ensure_minimal_user", fail_ensure)

    message = FakeMessage(
        text="/refset 1001 15",
        from_user=FakeTelegramUser(id=500, language_code="en"),
    )

    await refset_module.refset(
        message,
        settings=Settings(admin_ids=[500]),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert message.answers == [{"text": "Something went wrong. Please try again later."}]
