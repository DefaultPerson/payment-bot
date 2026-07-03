from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, get_type_hints

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.handlers import create_router
from bot.handlers.start import build_start_text, start


@dataclass(slots=True)
class FakeTelegramUser:
    id: int
    username: str | None = None
    first_name: str | None = None
    language_code: str | None = None


@dataclass(slots=True)
class FakeChat:
    id: int


class FakeMessage:
    def __init__(
        self,
        *,
        from_user: FakeTelegramUser | None = None,
        chat: FakeChat | None = None,
    ) -> None:
        self.from_user = from_user
        self.chat = chat
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
class PersistedUser:
    tg_id: int
    username: str | None
    first_name: str | None
    lang_code: str
    id: int = 0


async def _fake_no_subscription(*args: Any, **kwargs: Any) -> None:
    return None


@dataclass(slots=True)
class UpsertedUser:
    user: PersistedUser
    created: bool


def test_build_start_text_uses_english_for_english_user_language() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "en", status_label="Free")

    assert "<b>Welcome</b> to the <b>Hydra Monitors</b> payment bot." in text
    assert "<b>Status:</b> <code>Free</code>" in text


def test_build_start_text_falls_back_to_russian_for_unsupported_language() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "de", status_label="Free")

    assert "<b>Добро пожаловать</b>" in text


def test_build_start_text_maps_telegram_uk_to_ukrainian_locale() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "uk", status_label="Free")

    assert "<b>Ласкаво просимо</b>" in text


def test_build_start_text_maps_regional_english_tag_to_english_locale() -> None:
    i18n = JsonI18n()
    settings = Settings(bot={"support_username": "HydraSupport"})

    text = build_start_text(i18n, settings, "en-US", status_label="Free")

    assert "<b>Welcome</b> to the <b>Hydra Monitors</b> payment bot." in text


def test_create_router_does_not_require_dependencies() -> None:
    router = create_router()

    assert router.name == "handlers"


def test_start_handler_has_aiogram_di_compatible_signature() -> None:
    signature = inspect.signature(start)
    type_hints = get_type_hints(start)

    assert list(signature.parameters) == [
        "message",
        "settings",
        "i18n",
        "session_factory",
        "bot",
        "state",
    ]
    assert type_hints["settings"] is Settings
    assert type_hints["i18n"] is JsonI18n
    assert type_hints["session_factory"] == async_sessionmaker[AsyncSession]
    assert type_hints["bot"] is Bot
    # state is aiogram's FSMContext (optional so direct unit calls can omit it).
    assert type_hints["state"] == (FSMContext | None)


async def test_start_calls_user_upsert_commands_and_answers_main_menu(monkeypatch) -> None:
    import bot.handlers.start as start_module

    upsert_calls: list[dict[str, Any]] = []
    command_calls: list[dict[str, Any]] = []
    menu_calls: list[dict[str, Any]] = []
    menu_markup = object()

    async def fake_upsert(session: FakeSession, **kwargs: Any) -> UpsertedUser:
        upsert_calls.append({"session": session, **kwargs})
        return UpsertedUser(
            user=PersistedUser(
                tg_id=kwargs["tg_id"],
                username=kwargs["username"],
                first_name=kwargs["first_name"],
                lang_code="en",
            ),
            created=True,
        )

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        command_calls.append({"args": args, **kwargs})

    def fake_main_menu_keyboard(*args: Any, **kwargs: Any) -> object:
        menu_calls.append({"args": args, **kwargs})
        return menu_markup

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fake_upsert)
    monkeypatch.setattr(start_module, "set_user_commands", fake_set_user_commands)
    monkeypatch.setattr(start_module, "main_menu_keyboard", fake_main_menu_keyboard)
    monkeypatch.setattr(start_module, "get_subscription_for_user", _fake_no_subscription)

    settings = Settings(admin_ids=[1001], bot={"support_username": "HydraSupport"})
    i18n = JsonI18n()
    session_factory = FakeSessionFactory()
    bot = object()
    message = FakeMessage(
        from_user=FakeTelegramUser(
            id=1001,
            username="hydra_user",
            first_name="Hydra",
            language_code="uk",
        ),
        chat=FakeChat(id=777),
    )

    await start(message, settings=settings, i18n=i18n, session_factory=session_factory, bot=bot)

    assert upsert_calls == [
        {
            "session": session_factory.session,
            "tg_id": 1001,
            "username": "hydra_user",
            "first_name": "Hydra",
            "language_code": "uk",
            "i18n": i18n,
        }
    ]
    assert session_factory.session.commits == 1
    assert command_calls == [
        {
            "args": (bot, i18n, settings),
            "chat_id": 777,
            "lang_code": "en",
            "is_admin": True,
        }
    ]
    assert menu_calls == [
        {
            "args": (i18n, "en", settings),
            "is_admin": True,
            "show_trial": False,
        }
    ]
    assert len(message.answers) == 1
    assert "Hydra Monitors" in message.answers[0]["text"]
    assert "<b>Status:</b> <code>Free</code>" in message.answers[0]["text"]
    assert message.answers[0]["reply_markup"] is menu_markup


async def test_start_clears_pending_fsm_state(monkeypatch) -> None:
    import bot.handlers.start as start_module

    class FakeState:
        def __init__(self) -> None:
            self.cleared = False

        async def clear(self) -> None:
            self.cleared = True

    async def fake_upsert(session: FakeSession, **kwargs: Any) -> UpsertedUser:
        return UpsertedUser(
            user=PersistedUser(
                tg_id=kwargs["tg_id"],
                username=None,
                first_name=None,
                lang_code="en",
            ),
            created=False,
        )

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fake_upsert)
    monkeypatch.setattr(start_module, "set_user_commands", _noop_async)
    monkeypatch.setattr(start_module, "main_menu_keyboard", lambda *a, **k: object())
    monkeypatch.setattr(start_module, "get_subscription_for_user", _fake_no_subscription)

    state = FakeState()
    message = FakeMessage(
        from_user=FakeTelegramUser(id=1, language_code="en"),
        chat=FakeChat(id=1),
    )

    await start(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
        state=state,
    )

    # /start is the universal reset: a lingering inline-promo FSM must be cleared.
    assert state.cleared is True


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    return None


async def test_start_does_not_upsert_or_set_commands_when_from_user_is_missing(
    monkeypatch,
) -> None:
    import bot.handlers.start as start_module

    upsert_calls: list[dict[str, Any]] = []
    command_calls: list[dict[str, Any]] = []

    async def fake_upsert(session: FakeSession, **kwargs: Any) -> UpsertedUser:
        upsert_calls.append({"session": session, **kwargs})
        return UpsertedUser(
            user=PersistedUser(
                tg_id=kwargs["tg_id"],
                username=kwargs["username"],
                first_name=kwargs["first_name"],
                lang_code="ru",
            ),
            created=True,
        )

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        command_calls.append({"args": args, **kwargs})

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fake_upsert)
    monkeypatch.setattr(start_module, "set_user_commands", fake_set_user_commands)

    message = FakeMessage(from_user=None, chat=FakeChat(id=555))
    settings = Settings(admin_ids=[555], bot={"support_username": "HydraSupport"})
    i18n = JsonI18n()
    session_factory = FakeSessionFactory()

    await start(
        message,
        settings=settings,
        i18n=i18n,
        session_factory=session_factory,
        bot=object(),
    )

    assert upsert_calls == []
    assert command_calls == []
    assert session_factory.calls == 0
    assert "Добро пожаловать" in message.answers[0]["text"]


async def test_start_without_from_user_or_chat_still_answers_without_db(monkeypatch) -> None:
    import bot.handlers.start as start_module

    async def fail_upsert(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("start should not upsert without a telegram id")

    async def fail_set_user_commands(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("start should not set commands without a chat id")

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fail_upsert)
    monkeypatch.setattr(start_module, "set_user_commands", fail_set_user_commands)

    message = FakeMessage(from_user=None, chat=None)

    await start(
        message,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert "Добро пожаловать" in message.answers[0]["text"]


async def test_start_surfaces_database_errors_as_localized_reply(monkeypatch) -> None:
    import bot.handlers.start as start_module

    async def fail_upsert(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("database unavailable")

    async def fail_set_user_commands(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("commands must not be set after DB failure")

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fail_upsert)
    monkeypatch.setattr(start_module, "set_user_commands", fail_set_user_commands)

    message = FakeMessage(
        from_user=FakeTelegramUser(id=1001, language_code="en"),
        chat=FakeChat(id=1001),
    )
    session_factory = FakeSessionFactory()

    await start(
        message,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=session_factory,
        bot=object(),
    )

    assert session_factory.session.rollbacks == 1
    assert message.answers == [{"text": "Something went wrong. Please try again later."}]
