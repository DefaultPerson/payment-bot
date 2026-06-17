from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from sqlalchemy.exc import SQLAlchemyError

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards import CB_BUY, CB_ENTER_CHAT, CB_PROMO, CB_REFERRAL, CB_RENEW


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
        self.edits: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append({"text": text, **kwargs})


class FakeInaccessibleMessage:
    def __init__(self, *, chat: FakeChat | None = None) -> None:
        self.chat = chat


class FakeBadRequestMessage:
    def __init__(self, *, chat: FakeChat | None = None) -> None:
        self.chat = chat

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        raise TelegramBadRequest(
            method=SendMessage(chat_id=self.chat.id if self.chat else 0, text=text),
            message="message is not editable",
        )


class FakeCallback:
    def __init__(
        self,
        *,
        data: str,
        from_user: FakeTelegramUser | None = None,
        message: FakeMessage | FakeInaccessibleMessage | FakeBadRequestMessage | None = None,
    ) -> None:
        self.data = data
        self.from_user = from_user
        self.message = message
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
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
    lang_code: str


async def test_profile_command_fetches_subscription_and_answers_formatted_profile(
    monkeypatch,
) -> None:
    import bot.handlers.profile as profile_module

    calls: list[dict[str, Any]] = []
    user = FakeUserRecord(id=11, tg_id=1001, lang_code="ua")
    subscription = object()
    view = object()
    profile_markup = object()

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        calls.append({"name": "ensure", "session": session, **kwargs})
        return user

    async def fake_get_subscription_for_user(
        session: FakeSession,
        user_id: int,
    ) -> object:
        calls.append({"name": "subscription", "session": session, "user_id": user_id})
        return subscription

    def fake_build_profile_view(*args: Any, **kwargs: Any) -> object:
        calls.append({"name": "build", "args": args, **kwargs})
        return view

    def fake_format_profile_text(*args: Any, **kwargs: Any) -> str:
        calls.append({"name": "format", "args": args, **kwargs})
        return "formatted profile"

    def fake_profile_keyboard(*args: Any, **kwargs: Any) -> object:
        calls.append({"name": "keyboard", "args": args, **kwargs})
        return profile_markup

    monkeypatch.setattr(profile_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(profile_module, "get_subscription_for_user", fake_get_subscription_for_user)
    monkeypatch.setattr(profile_module, "build_profile_view", fake_build_profile_view)
    monkeypatch.setattr(profile_module, "format_profile_text", fake_format_profile_text)
    monkeypatch.setattr(profile_module, "profile_keyboard", fake_profile_keyboard)

    settings = Settings()
    i18n = JsonI18n()
    session_factory = FakeSessionFactory()
    message = FakeMessage(
        from_user=FakeTelegramUser(id=1001, language_code="uk"),
        chat=FakeChat(id=1001),
    )

    await profile_module.profile(
        message,
        settings=settings,
        i18n=i18n,
        session_factory=session_factory,
    )

    assert calls[0] == {
        "name": "ensure",
        "session": session_factory.session,
        "tg_id": 1001,
        "i18n": i18n,
        "language_code": "uk",
    }
    assert calls[1] == {
        "name": "subscription",
        "session": session_factory.session,
        "user_id": 11,
    }
    assert calls[2]["name"] == "build"
    assert calls[2]["args"] == (user, subscription)
    assert calls[2]["now"].tzinfo is not None
    assert calls[3] == {"name": "format", "args": (view, i18n, "ua")}
    assert calls[4] == {"name": "keyboard", "args": (i18n, settings, "ua")}
    assert session_factory.session.commits == 1
    assert message.answers == [{"text": "formatted profile", "reply_markup": profile_markup}]


async def test_profile_command_surfaces_database_errors_as_localized_reply(monkeypatch) -> None:
    import bot.handlers.profile as profile_module

    async def fail_ensure_minimal_user(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(profile_module, "ensure_minimal_user", fail_ensure_minimal_user)

    session_factory = FakeSessionFactory()
    message = FakeMessage(
        from_user=FakeTelegramUser(id=1001, language_code="en"),
        chat=FakeChat(id=1001),
    )

    await profile_module.profile(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=session_factory,
    )

    assert session_factory.session.rollbacks == 1
    assert message.answers == [{"text": "Something went wrong. Please try again later."}]


async def test_profile_callback_edits_message_and_acknowledges(monkeypatch) -> None:
    import bot.handlers.profile as profile_module

    monkeypatch.setattr(
        profile_module,
        "ensure_minimal_user",
        lambda *args, **kwargs: _async_return(FakeUserRecord(id=7, tg_id=700, lang_code="en")),
    )
    monkeypatch.setattr(
        profile_module,
        "get_subscription_for_user",
        lambda *args, **kwargs: _async_return(None),
    )
    monkeypatch.setattr(profile_module, "build_profile_view", lambda *args, **kwargs: object())
    monkeypatch.setattr(profile_module, "format_profile_text", lambda *args, **kwargs: "profile")
    monkeypatch.setattr(profile_module, "profile_keyboard", lambda *args, **kwargs: "keyboard")

    callback_message = FakeMessage(from_user=None, chat=FakeChat(id=700))
    callback = FakeCallback(
        data="profile",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await profile_module.profile_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [{"text": None}]
    assert callback_message.edits == [{"text": "profile", "reply_markup": "keyboard"}]


async def test_profile_callback_answers_profile_when_message_is_not_editable(monkeypatch) -> None:
    import bot.handlers.profile as profile_module

    monkeypatch.setattr(
        profile_module,
        "ensure_minimal_user",
        lambda *args, **kwargs: _async_return(FakeUserRecord(id=7, tg_id=700, lang_code="en")),
    )
    monkeypatch.setattr(
        profile_module,
        "get_subscription_for_user",
        lambda *args, **kwargs: _async_return(None),
    )
    monkeypatch.setattr(profile_module, "build_profile_view", lambda *args, **kwargs: object())
    monkeypatch.setattr(profile_module, "format_profile_text", lambda *args, **kwargs: "profile")
    monkeypatch.setattr(profile_module, "profile_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="profile",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeInaccessibleMessage(chat=FakeChat(id=700)),
    )

    await profile_module.profile_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [{"text": "profile", "show_alert": True}]


async def test_profile_callback_bounds_long_non_editable_answer(monkeypatch) -> None:
    import bot.handlers.profile as profile_module

    long_profile_text = "Profile\n\n" + "Active subscription details. " * 20

    monkeypatch.setattr(
        profile_module,
        "ensure_minimal_user",
        lambda *args, **kwargs: _async_return(FakeUserRecord(id=7, tg_id=700, lang_code="en")),
    )
    monkeypatch.setattr(
        profile_module,
        "get_subscription_for_user",
        lambda *args, **kwargs: _async_return(None),
    )
    monkeypatch.setattr(profile_module, "build_profile_view", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        profile_module,
        "format_profile_text",
        lambda *args, **kwargs: long_profile_text,
    )
    monkeypatch.setattr(profile_module, "profile_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="profile",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeInaccessibleMessage(chat=FakeChat(id=700)),
    )

    await profile_module.profile_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    answer_text = callback.answers[0]["text"]
    assert answer_text
    assert answer_text.startswith("Profile")
    assert answer_text.endswith("...")
    assert len(answer_text) <= 200


async def test_profile_callback_answers_when_edit_raises_bad_request(monkeypatch) -> None:
    import bot.handlers.profile as profile_module

    monkeypatch.setattr(
        profile_module,
        "ensure_minimal_user",
        lambda *args, **kwargs: _async_return(FakeUserRecord(id=7, tg_id=700, lang_code="en")),
    )
    monkeypatch.setattr(
        profile_module,
        "get_subscription_for_user",
        lambda *args, **kwargs: _async_return(None),
    )
    monkeypatch.setattr(profile_module, "build_profile_view", lambda *args, **kwargs: object())
    monkeypatch.setattr(profile_module, "format_profile_text", lambda *args, **kwargs: "profile")
    monkeypatch.setattr(profile_module, "profile_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="profile",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeBadRequestMessage(chat=FakeChat(id=700)),
    )

    await profile_module.profile_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [{"text": "profile", "show_alert": True}]


async def test_language_command_answers_with_language_keyboard(monkeypatch) -> None:
    import bot.handlers.language as language_module

    keyboard = object()

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=500, lang_code="en")

    async def fake_upsert_user_from_telegram(
        session: FakeSession,
        **kwargs: Any,
    ) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=kwargs["tg_id"], lang_code="en")

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(
        language_module,
        "upsert_user_from_telegram",
        fake_upsert_user_from_telegram,
        raising=False,
    )
    monkeypatch.setattr(language_module, "language_keyboard", lambda *args, **kwargs: keyboard)

    message = FakeMessage(
        from_user=FakeTelegramUser(id=500, language_code="uk"),
        chat=FakeChat(id=500),
    )

    await language_module.language(
        message,
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert message.answers == [{"text": "Change interface language", "reply_markup": keyboard}]


async def test_language_command_preserves_new_user_telegram_locale(monkeypatch) -> None:
    import bot.handlers.language as language_module

    upsert_calls: list[dict[str, Any]] = []

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=500, lang_code="ru")

    async def fake_upsert_user_from_telegram(
        session: FakeSession,
        **kwargs: Any,
    ) -> FakeUserRecord:
        upsert_calls.append({"session": session, **kwargs})
        return FakeUserRecord(id=5, tg_id=kwargs["tg_id"], lang_code="en")

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(
        language_module,
        "upsert_user_from_telegram",
        fake_upsert_user_from_telegram,
        raising=False,
    )
    monkeypatch.setattr(language_module, "language_keyboard", lambda *args, **kwargs: "keyboard")

    message = FakeMessage(
        from_user=FakeTelegramUser(
            id=500,
            username="new_user",
            first_name="New",
            language_code="en",
        ),
        chat=FakeChat(id=500),
    )
    i18n = JsonI18n()
    session_factory = FakeSessionFactory()

    await language_module.language(message, i18n=i18n, session_factory=session_factory)

    assert upsert_calls == [
        {
            "session": session_factory.session,
            "tg_id": 500,
            "username": "new_user",
            "first_name": "New",
            "language_code": "en",
            "i18n": i18n,
        }
    ]
    assert message.answers == [{"text": "Change interface language", "reply_markup": "keyboard"}]


async def test_language_command_surfaces_database_errors_as_localized_reply(monkeypatch) -> None:
    import bot.handlers.language as language_module

    async def fail_upsert_user_from_telegram(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        language_module,
        "upsert_user_from_telegram",
        fail_upsert_user_from_telegram,
        raising=False,
    )

    session_factory = FakeSessionFactory()
    message = FakeMessage(
        from_user=FakeTelegramUser(id=500, language_code="en"),
        chat=FakeChat(id=500),
    )

    await language_module.language(
        message,
        i18n=JsonI18n(),
        session_factory=session_factory,
    )

    assert session_factory.session.rollbacks == 1
    assert message.answers == [{"text": "Something went wrong. Please try again later."}]


async def test_language_menu_callback_answers_language_menu_when_message_is_missing(
    monkeypatch,
) -> None:
    import bot.handlers.language as language_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=500, lang_code="en")

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(language_module, "language_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="language",
        from_user=FakeTelegramUser(id=500, language_code="uk"),
        message=None,
    )

    await language_module.language_menu_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [{"text": "Change interface language", "show_alert": True}]


async def test_language_callback_updates_language_and_commands(monkeypatch) -> None:
    import bot.handlers.language as language_module

    ensure_calls: list[dict[str, Any]] = []
    update_calls: list[dict[str, Any]] = []
    command_calls: list[dict[str, Any]] = []
    menu_markup = object()

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        ensure_calls.append({"session": session, **kwargs})
        return FakeUserRecord(id=5, tg_id=500, lang_code="ru")

    async def fake_update_user_language(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        update_calls.append({"session": session, **kwargs})
        return FakeUserRecord(id=5, tg_id=kwargs["tg_id"], lang_code=kwargs["lang_code"])

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        command_calls.append({"args": args, **kwargs})

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(language_module, "update_user_language", fake_update_user_language)
    monkeypatch.setattr(language_module, "set_user_commands", fake_set_user_commands)
    monkeypatch.setattr(language_module, "main_menu_keyboard", lambda *args, **kwargs: menu_markup)

    settings = Settings(admin_ids=[500], bot={"support_username": "HydraSupport"})
    i18n = JsonI18n()
    session_factory = FakeSessionFactory()
    bot = object()
    callback_message = FakeMessage(from_user=None, chat=FakeChat(id=500))
    callback = FakeCallback(
        data="lang:en",
        from_user=FakeTelegramUser(id=500, language_code="ru"),
        message=callback_message,
    )

    await language_module.language_callback(
        callback,
        settings=settings,
        i18n=i18n,
        session_factory=session_factory,
        bot=bot,
    )

    assert ensure_calls == [
        {
            "session": session_factory.session,
            "tg_id": 500,
            "i18n": i18n,
            "language_code": "ru",
        }
    ]
    assert update_calls == [{"session": session_factory.session, "tg_id": 500, "lang_code": "en"}]
    assert session_factory.session.commits == 1
    assert command_calls == [
        {
            "args": (bot, i18n, settings),
            "chat_id": 500,
            "lang_code": "en",
            "is_admin": True,
        }
    ]
    assert callback.answers == [{"text": "Language changed to English."}]
    assert callback_message.edits == [
        {
            "text": "Welcome to Hydra Monitors payment bot.\n\nSupport: https://t.me/HydraSupport",
            "reply_markup": menu_markup,
        }
    ]


async def test_language_callback_answers_main_menu_when_message_is_not_editable(
    monkeypatch,
) -> None:
    import bot.handlers.language as language_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=500, lang_code="ru")

    async def fake_update_user_language(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=kwargs["tg_id"], lang_code=kwargs["lang_code"])

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(language_module, "update_user_language", fake_update_user_language)
    monkeypatch.setattr(language_module, "set_user_commands", fake_set_user_commands)
    monkeypatch.setattr(language_module, "main_menu_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="lang:en",
        from_user=FakeTelegramUser(id=500, language_code="ru"),
        message=FakeInaccessibleMessage(chat=FakeChat(id=500)),
    )

    await language_module.language_callback(
        callback,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert callback.answers == [
        {
            "text": "Welcome to Hydra Monitors payment bot.\n\nSupport: https://t.me/HydraSupport",
            "show_alert": True,
        },
    ]


async def test_language_callback_answers_when_edit_raises_bad_request(monkeypatch) -> None:
    import bot.handlers.language as language_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=500, lang_code="ru")

    async def fake_update_user_language(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=5, tg_id=kwargs["tg_id"], lang_code=kwargs["lang_code"])

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(language_module, "update_user_language", fake_update_user_language)
    monkeypatch.setattr(language_module, "set_user_commands", fake_set_user_commands)
    monkeypatch.setattr(language_module, "main_menu_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="lang:en",
        from_user=FakeTelegramUser(id=500, language_code="ru"),
        message=FakeBadRequestMessage(chat=FakeChat(id=500)),
    )

    await language_module.language_callback(
        callback,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert callback.answers == [
        {
            "text": "Welcome to Hydra Monitors payment bot.\n\nSupport: https://t.me/HydraSupport",
            "show_alert": True,
        },
    ]


async def test_invalid_language_callback_returns_generic_error_without_update(monkeypatch) -> None:
    import bot.handlers.language as language_module

    async def fail_update(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("invalid language callback must not update DB")

    monkeypatch.setattr(language_module, "update_user_language", fail_update)

    callback = FakeCallback(
        data="lang:de",
        from_user=FakeTelegramUser(id=500, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=500)),
    )

    await language_module.language_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert callback.answers == [
        {"text": "Something went wrong. Please try again later.", "show_alert": True}
    ]


async def test_support_command_answers_with_support_keyboard(monkeypatch) -> None:
    import bot.handlers.support as support_module

    keyboard = object()

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=8, tg_id=800, lang_code="en")

    async def fake_upsert_user_from_telegram(
        session: FakeSession,
        **kwargs: Any,
    ) -> FakeUserRecord:
        return FakeUserRecord(id=8, tg_id=kwargs["tg_id"], lang_code="en")

    monkeypatch.setattr(support_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(
        support_module,
        "upsert_user_from_telegram",
        fake_upsert_user_from_telegram,
        raising=False,
    )
    monkeypatch.setattr(support_module, "support_keyboard", lambda *args, **kwargs: keyboard)

    settings = Settings(bot={"support_username": "HydraSupport"})
    message = FakeMessage(
        from_user=FakeTelegramUser(id=800, language_code="ru"),
        chat=FakeChat(id=800),
    )

    await support_module.support(
        message,
        settings=settings,
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert message.answers == [
        {
            "text": "Support is available here: https://t.me/HydraSupport",
            "reply_markup": keyboard,
        }
    ]


async def test_support_command_preserves_new_user_telegram_locale(monkeypatch) -> None:
    import bot.handlers.support as support_module

    upsert_calls: list[dict[str, Any]] = []

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=8, tg_id=800, lang_code="ru")

    async def fake_upsert_user_from_telegram(
        session: FakeSession,
        **kwargs: Any,
    ) -> FakeUserRecord:
        upsert_calls.append({"session": session, **kwargs})
        return FakeUserRecord(id=8, tg_id=kwargs["tg_id"], lang_code="ua")

    monkeypatch.setattr(support_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(
        support_module,
        "upsert_user_from_telegram",
        fake_upsert_user_from_telegram,
        raising=False,
    )
    monkeypatch.setattr(support_module, "support_keyboard", lambda *args, **kwargs: "keyboard")

    settings = Settings(bot={"support_username": "HydraSupport"})
    message = FakeMessage(
        from_user=FakeTelegramUser(
            id=800,
            username="new_user",
            first_name="New",
            language_code="uk",
        ),
        chat=FakeChat(id=800),
    )
    i18n = JsonI18n()
    session_factory = FakeSessionFactory()

    await support_module.support(
        message,
        settings=settings,
        i18n=i18n,
        session_factory=session_factory,
    )

    assert upsert_calls == [
        {
            "session": session_factory.session,
            "tg_id": 800,
            "username": "new_user",
            "first_name": "New",
            "language_code": "uk",
            "i18n": i18n,
        }
    ]
    assert message.answers == [
        {
            "text": "Підтримка доступна тут: https://t.me/HydraSupport",
            "reply_markup": "keyboard",
        }
    ]


async def test_support_command_surfaces_database_errors_as_localized_reply(monkeypatch) -> None:
    import bot.handlers.support as support_module

    async def fail_upsert_user_from_telegram(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        support_module,
        "upsert_user_from_telegram",
        fail_upsert_user_from_telegram,
        raising=False,
    )

    session_factory = FakeSessionFactory()
    message = FakeMessage(
        from_user=FakeTelegramUser(id=800, language_code="en"),
        chat=FakeChat(id=800),
    )

    await support_module.support(
        message,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=session_factory,
    )

    assert session_factory.session.rollbacks == 1
    assert message.answers == [{"text": "Something went wrong. Please try again later."}]


async def test_support_callback_edits_support_text_and_keyboard(monkeypatch) -> None:
    import bot.handlers.support as support_module

    monkeypatch.setattr(
        support_module,
        "ensure_minimal_user",
        lambda *args, **kwargs: _async_return(FakeUserRecord(id=9, tg_id=900, lang_code="ua")),
    )
    monkeypatch.setattr(support_module, "support_keyboard", lambda *args, **kwargs: "keyboard")

    callback_message = FakeMessage(chat=FakeChat(id=900))
    callback = FakeCallback(
        data="support",
        from_user=FakeTelegramUser(id=900, language_code="en"),
        message=callback_message,
    )

    await support_module.support_callback(
        callback,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [{"text": None}]
    assert callback_message.edits == [
        {
            "text": "Підтримка доступна тут: https://t.me/HydraSupport",
            "reply_markup": "keyboard",
        }
    ]


async def test_support_callback_answers_support_text_when_message_is_not_editable(
    monkeypatch,
) -> None:
    import bot.handlers.support as support_module

    monkeypatch.setattr(
        support_module,
        "ensure_minimal_user",
        lambda *args, **kwargs: _async_return(FakeUserRecord(id=9, tg_id=900, lang_code="ua")),
    )
    monkeypatch.setattr(support_module, "support_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="support",
        from_user=FakeTelegramUser(id=900, language_code="en"),
        message=FakeInaccessibleMessage(chat=FakeChat(id=900)),
    )

    await support_module.support_callback(
        callback,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [
        {"text": "Підтримка доступна тут: https://t.me/HydraSupport", "show_alert": True}
    ]


async def test_support_callback_answers_when_edit_raises_bad_request(monkeypatch) -> None:
    import bot.handlers.support as support_module

    monkeypatch.setattr(
        support_module,
        "ensure_minimal_user",
        lambda *args, **kwargs: _async_return(FakeUserRecord(id=9, tg_id=900, lang_code="ua")),
    )
    monkeypatch.setattr(support_module, "support_keyboard", lambda *args, **kwargs: "keyboard")

    callback = FakeCallback(
        data="support",
        from_user=FakeTelegramUser(id=900, language_code="en"),
        message=FakeBadRequestMessage(chat=FakeChat(id=900)),
    )

    await support_module.support_callback(
        callback,
        settings=Settings(bot={"support_username": "HydraSupport"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [
        {"text": "Підтримка доступна тут: https://t.me/HydraSupport", "show_alert": True}
    ]


def test_buy_button_is_not_a_future_callback() -> None:
    import bot.handlers.support as support_module

    assert CB_BUY not in support_module.FUTURE_CALLBACKS


@pytest.mark.parametrize("callback_data", [CB_PROMO, CB_REFERRAL, CB_RENEW, CB_ENTER_CHAT])
async def test_future_buttons_answer_coming_soon_without_db_mutation(callback_data: str) -> None:
    import bot.handlers.support as support_module

    callback = FakeCallback(
        data=callback_data,
        from_user=FakeTelegramUser(id=1001, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=1001)),
    )

    await support_module.coming_soon_callback(callback, i18n=JsonI18n())

    assert callback.answers == [{"text": "This feature is coming soon.", "show_alert": False}]


async def _async_return(value: object) -> object:
    return value
