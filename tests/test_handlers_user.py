from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from sqlalchemy.exc import SQLAlchemyError

from bot.config import Settings
from bot.core.i18n import JsonI18n


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

    async def scalar(self, *args: object, **kwargs: object) -> None:
        # No subscription row in these fake-session unit tests; loyalty helpers read it as
        # "no active coverage" (effective status falls back to the stored/basic tier).
        return None


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
    referral_percent: Decimal | None = None


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
    monkeypatch.setattr(
        profile_module,
        "get_referral_stats",
        lambda *args, **kwargs: _async_return(object()),
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
    monkeypatch.setattr(
        profile_module,
        "get_referral_stats",
        lambda *args, **kwargs: _async_return(object()),
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
    monkeypatch.setattr(
        profile_module,
        "get_referral_stats",
        lambda *args, **kwargs: _async_return(object()),
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
    monkeypatch.setattr(
        profile_module,
        "get_referral_stats",
        lambda *args, **kwargs: _async_return(object()),
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

    async def fake_get_subscription(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(language_module, "update_user_language", fake_update_user_language)
    monkeypatch.setattr(language_module, "set_user_commands", fake_set_user_commands)
    monkeypatch.setattr(language_module, "get_subscription_for_user", fake_get_subscription)
    monkeypatch.setattr(language_module, "build_start_text", lambda *a, **k: "MENU_TEXT")
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
            "text": "MENU_TEXT",
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

    async def fake_get_subscription(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(language_module, "update_user_language", fake_update_user_language)
    monkeypatch.setattr(language_module, "set_user_commands", fake_set_user_commands)
    monkeypatch.setattr(language_module, "get_subscription_for_user", fake_get_subscription)
    monkeypatch.setattr(language_module, "build_start_text", lambda *a, **k: "MENU_TEXT")
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
            "text": "MENU_TEXT",
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

    async def fake_get_subscription(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(language_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(language_module, "update_user_language", fake_update_user_language)
    monkeypatch.setattr(language_module, "set_user_commands", fake_set_user_commands)
    monkeypatch.setattr(language_module, "get_subscription_for_user", fake_get_subscription)
    monkeypatch.setattr(language_module, "build_start_text", lambda *a, **k: "MENU_TEXT")
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
            "text": "MENU_TEXT",
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


async def _async_return(value: object) -> object:
    return value


@dataclass(slots=True)
class FakeSubscription:
    expires_at: Any
    plan_code: str | None = None


async def test_enter_chat_sends_access_links_for_active_subscription(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    import bot.handlers.profile as profile_module

    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        profile_module,
        "ensure_minimal_user",
        lambda *a, **k: _async_return(FakeUserRecord(id=7, tg_id=700, lang_code="en")),
    )
    subscription = FakeSubscription(
        expires_at=datetime.now(UTC) + timedelta(days=5), plan_code="pro"
    )
    monkeypatch.setattr(
        profile_module,
        "get_subscription_for_user",
        lambda *a, **k: _async_return(subscription),
    )

    async def fake_send_access_links(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(profile_module, "send_access_links", fake_send_access_links)

    profile_message = FakeMessage(from_user=None, chat=FakeChat(id=700))
    profile_message.message_id = 999
    callback = FakeCallback(
        data="enter_chat",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=profile_message,
    )

    await profile_module.enter_chat_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert len(calls) == 1
    assert calls[0]["plan_code"] == "pro"
    assert calls[0]["tg_id"] == 700
    # The profile message is edited in place into the links (no "Access links sent" toast).
    assert calls[0]["edit_message_id"] == 999
    assert callback.answers == [{"text": None}]


async def test_enter_chat_alerts_without_active_subscription(monkeypatch) -> None:
    import bot.handlers.profile as profile_module

    monkeypatch.setattr(
        profile_module,
        "ensure_minimal_user",
        lambda *a, **k: _async_return(FakeUserRecord(id=7, tg_id=700, lang_code="en")),
    )
    monkeypatch.setattr(
        profile_module,
        "get_subscription_for_user",
        lambda *a, **k: _async_return(None),
    )

    async def fail_send(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not send access links without an active subscription")

    monkeypatch.setattr(profile_module, "send_access_links", fail_send)

    callback = FakeCallback(
        data="enter_chat",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeMessage(from_user=None, chat=FakeChat(id=700)),
    )

    await profile_module.enter_chat_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert callback.answers == [
        {
            "text": "No active subscription. Get a subscription to unlock chat access.",
            "show_alert": True,
        }
    ]
