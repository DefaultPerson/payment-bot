from __future__ import annotations

from typing import Any

from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.core.menu import configure_commands, set_user_commands


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []

    async def set_my_commands(
        self,
        commands: Any,
        *,
        scope: Any = None,
        language_code: str | None = None,
    ) -> None:
        self.calls.append({"commands": commands, "scope": scope, "language_code": language_code})

    async def delete_my_commands(
        self,
        *,
        scope: Any = None,
        language_code: str | None = None,
    ) -> None:
        self.deletes.append({"scope": scope, "language_code": language_code})


async def test_configure_commands_registers_default_and_localized_menus() -> None:
    bot = FakeBot()
    i18n = JsonI18n()
    settings = Settings()  # supported ["ru", "ua", "en"], fallback "ru"

    await configure_commands(bot, i18n, settings)

    # One default list (no language_code) plus one per non-fallback supported language.
    assert len(bot.calls) == 3

    default_call = bot.calls[0]
    assert default_call["language_code"] is None
    assert isinstance(default_call["scope"], BotCommandScopeAllPrivateChats)
    assert [command.description for command in default_call["commands"]] == [
        i18n.gettext("commands.start", "ru"),
        i18n.gettext("commands.language", "ru"),
    ]

    localized = {call["language_code"]: call for call in bot.calls[1:]}
    # ``ua`` is registered under the Telegram ``uk`` language tag; ``en`` stays ``en``.
    assert set(localized) == {"uk", "en"}
    assert isinstance(localized["uk"]["scope"], BotCommandScopeAllPrivateChats)
    assert [command.description for command in localized["uk"]["commands"]] == [
        i18n.gettext("commands.start", "ua"),
        i18n.gettext("commands.language", "ua"),
    ]
    assert [command.description for command in localized["en"]["commands"]] == [
        i18n.gettext("commands.start", "en"),
        i18n.gettext("commands.language", "en"),
    ]


async def test_configure_commands_swallows_telegram_errors() -> None:
    class FailingBot:
        async def set_my_commands(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("telegram down")

    # A failed command registration must not crash startup.
    await configure_commands(FailingBot(), JsonI18n(), Settings())


async def test_set_user_commands_uses_chat_scope_without_language_code() -> None:
    bot = FakeBot()
    i18n = JsonI18n()
    settings = Settings()  # supported ["ru", "ua", "en"]

    await set_user_commands(
        bot, i18n, settings, chat_id=777, lang_code="ru", is_admin=False
    )

    # A single chat-scoped set with NO language_code makes the in-app language authoritative
    # for this chat regardless of the Telegram client locale.
    assert len(bot.calls) == 1
    call = bot.calls[0]
    assert isinstance(call["scope"], BotCommandScopeChat)
    assert call["scope"].chat_id == 777
    assert call["language_code"] is None
    assert [command.description for command in call["commands"]] == [
        i18n.gettext("commands.start", "ru"),
        i18n.gettext("commands.language", "ru"),
    ]
    # Stale per-language chat scopes are cleared (ua→uk, en→en, ru→ru).
    assert {d["language_code"] for d in bot.deletes} == {"uk", "en", "ru"}
    assert all(isinstance(d["scope"], BotCommandScopeChat) for d in bot.deletes)
    assert all(d["scope"].chat_id == 777 for d in bot.deletes)
