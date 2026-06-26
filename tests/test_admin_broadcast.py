from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import bot.handlers.admin.broadcast as broadcast_module
from bot.config import Settings
from bot.core.i18n import JsonI18n

ADMIN_ID = 500


class FakeTelegramUser:
    def __init__(self, user_id: int, language_code: str | None = "en") -> None:
        self.id = user_id
        self.language_code = language_code


class FakeChat:
    def __init__(self, chat_type: str = "private") -> None:
        self.type = chat_type


class FakeMessage:
    def __init__(
        self,
        *,
        from_user: FakeTelegramUser | None = None,
        chat_type: str = "private",
    ) -> None:
        self.from_user = from_user
        self.chat = FakeChat(chat_type)
        self.answers: list[dict[str, Any]] = []
        self.deleted = False

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})

    async def delete(self) -> None:
        self.deleted = True


class FakeCallback:
    def __init__(
        self,
        *,
        data: str,
        from_user: FakeTelegramUser | None = None,
        message: FakeMessage | None = None,
    ) -> None:
        self.data = data
        self.from_user = from_user
        self.message = message
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


class FakeBroadcastUI:
    def __init__(self) -> None:
        self.opened: list[int] | None = None

    async def open_menu(self, subscriber_ids: list[int], return_callback: Any = None) -> None:
        self.opened = list(subscriber_ids)


def _settings() -> Settings:
    return Settings(admin_ids=[ADMIN_ID])


async def test_broadcast_entry_in_private_posts_segment_keyboard() -> None:
    message = FakeMessage(from_user=FakeTelegramUser(ADMIN_ID, "en"), chat_type="private")
    callback = FakeCallback(
        data="adm:bcast",
        from_user=FakeTelegramUser(ADMIN_ID, "en"),
        message=message,
    )

    await broadcast_module.broadcast_entry(callback, settings=_settings(), i18n=JsonI18n())

    assert len(message.answers) == 1
    answer = message.answers[0]
    assert answer["text"] == "Who should receive the broadcast?"
    buttons = [button for row in answer["reply_markup"].inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == [
        "bcseg:all",
        "bcseg:active",
        "bcseg:inactive",
    ]
    assert callback.answers == [{"text": None}]


async def test_broadcast_entry_outside_private_is_rejected() -> None:
    message = FakeMessage(from_user=FakeTelegramUser(ADMIN_ID, "en"), chat_type="supergroup")
    callback = FakeCallback(
        data="adm:bcast",
        from_user=FakeTelegramUser(ADMIN_ID, "en"),
        message=message,
    )

    await broadcast_module.broadcast_entry(callback, settings=_settings(), i18n=JsonI18n())

    assert message.answers == []
    assert callback.answers == [
        {"text": "Open the broadcast in a private chat with the bot.", "show_alert": True}
    ]


async def test_broadcast_entry_ignored_for_non_admin() -> None:
    message = FakeMessage(from_user=FakeTelegramUser(777, "en"), chat_type="private")
    callback = FakeCallback(
        data="adm:bcast", from_user=FakeTelegramUser(777, "en"), message=message
    )

    await broadcast_module.broadcast_entry(callback, settings=_settings(), i18n=JsonI18n())

    assert message.answers == []
    assert callback.answers == [{"text": None}]


async def test_segment_callback_opens_menu_with_segment_targets(monkeypatch) -> None:
    captured_segment: list[str] = []

    async def fake_targets(session: Any, *, segment: str, now: Any) -> list[int]:
        captured_segment.append(segment)
        return [1001, 1002]

    @asynccontextmanager
    async def fake_scope(_factory: Any):
        yield object()

    monkeypatch.setattr(broadcast_module, "get_broadcast_targets", fake_targets)
    monkeypatch.setattr(broadcast_module, "session_scope", fake_scope)

    message = FakeMessage(from_user=FakeTelegramUser(ADMIN_ID, "en"))
    callback = FakeCallback(
        data="bcseg:active",
        from_user=FakeTelegramUser(ADMIN_ID, "en"),
        message=message,
    )
    ui = FakeBroadcastUI()

    await broadcast_module.broadcast_segment_callback(
        callback,
        settings=_settings(),
        i18n=JsonI18n(),
        session_factory=object(),
        broadcast_ui=ui,
    )

    assert captured_segment == ["active"]
    assert ui.opened == [1001, 1002]
    assert message.deleted is True
    assert callback.answers == [{"text": None}]


async def test_segment_callback_rejects_non_admin() -> None:
    callback = FakeCallback(
        data="bcseg:all",
        from_user=FakeTelegramUser(777, "en"),
        message=FakeMessage(),
    )
    ui = FakeBroadcastUI()

    await broadcast_module.broadcast_segment_callback(
        callback,
        settings=_settings(),
        i18n=JsonI18n(),
        session_factory=object(),
        broadcast_ui=ui,
    )

    assert ui.opened is None
    assert callback.answers == [{"text": "Only admins can broadcast.", "show_alert": True}]


async def test_segment_callback_requires_private_chat_manager() -> None:
    callback = FakeCallback(
        data="bcseg:all",
        from_user=FakeTelegramUser(ADMIN_ID, "en"),
        message=FakeMessage(),
    )

    await broadcast_module.broadcast_segment_callback(
        callback,
        settings=_settings(),
        i18n=JsonI18n(),
        session_factory=object(),
        broadcast_ui=None,
    )

    assert callback.answers == [
        {"text": "Open the broadcast in a private chat with the bot.", "show_alert": True}
    ]


async def test_segment_callback_ignores_unknown_segment(monkeypatch) -> None:
    called = False

    async def fake_targets(*args: Any, **kwargs: Any) -> list[int]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(broadcast_module, "get_broadcast_targets", fake_targets)

    callback = FakeCallback(
        data="bcseg:bogus",
        from_user=FakeTelegramUser(ADMIN_ID, "en"),
        message=FakeMessage(),
    )
    ui = FakeBroadcastUI()

    await broadcast_module.broadcast_segment_callback(
        callback,
        settings=_settings(),
        i18n=JsonI18n(),
        session_factory=object(),
        broadcast_ui=ui,
    )

    assert called is False
    assert ui.opened is None
    assert callback.answers == [{"text": None}]
