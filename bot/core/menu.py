from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat

from bot.config import Settings
from bot.core.i18n import JsonI18n

logger = logging.getLogger(__name__)

USER_COMMANDS = ("start", "language")
# Admin actions are now driven entirely by the inline admin panel, not slash commands.
ADMIN_COMMANDS: tuple[str, ...] = ()
TELEGRAM_LANGUAGE_CODES = {
    "ua": "uk",
}


def build_commands(i18n: JsonI18n, lang_code: str, *, is_admin: bool) -> list[BotCommand]:
    commands = [*USER_COMMANDS]
    if is_admin:
        commands.extend(ADMIN_COMMANDS)

    return [
        BotCommand(command=command, description=i18n.gettext(f"commands.{command}", lang_code))
        for command in commands
    ]


async def configure_commands(bot: Bot, i18n: JsonI18n, settings: Settings) -> None:
    """Register the localized slash-command menu for every private chat at startup.

    Sets a default (fallback-language) command list on ``BotCommandScopeAllPrivateChats``
    plus one localized list per additionally supported language, so a Telegram client shows
    commands in the user's own language before any per-chat ``set_user_commands`` call. The
    per-chat call still runs on ``/start`` and ``/language`` to cover admin-only commands and
    language switches; this only seeds the language-scoped defaults.
    """
    fallback = settings.bot.fallback_language
    try:
        await bot.set_my_commands(
            build_commands(i18n, fallback, is_admin=False),
            scope=BotCommandScopeAllPrivateChats(),
        )
        for language in settings.bot.supported_languages:
            if language == fallback:
                continue
            await bot.set_my_commands(
                build_commands(i18n, language, is_admin=False),
                scope=BotCommandScopeAllPrivateChats(),
                language_code=TELEGRAM_LANGUAGE_CODES.get(language, language),
            )
    except Exception:
        logger.warning("failed to configure global bot commands", exc_info=True)


async def set_user_commands(
    bot: Bot,
    i18n: JsonI18n,
    settings: Settings,
    *,
    chat_id: int,
    lang_code: str,
    is_admin: bool,
) -> None:
    language = i18n.resolve_language(lang_code)
    if language not in settings.bot.supported_languages:
        language = settings.bot.fallback_language

    try:
        # Chat-scoped WITHOUT language_code so the IN-APP language choice is authoritative for
        # this chat regardless of the user's Telegram client locale. The previous per-locale
        # scope only applied when the client language happened to match the choice, so an
        # in-app switch did nothing. Then clear any stale per-language chat scopes left by that
        # behavior, so a client whose locale matches an old choice can't resurface them.
        await bot.set_my_commands(
            build_commands(i18n, language, is_admin=is_admin),
            scope=BotCommandScopeChat(chat_id=chat_id),
        )
        for other in settings.bot.supported_languages:
            await bot.delete_my_commands(
                scope=BotCommandScopeChat(chat_id=chat_id),
                language_code=TELEGRAM_LANGUAGE_CODES.get(other, other),
            )
    except Exception:
        logger.warning("failed to set bot commands for chat_id=%s", chat_id, exc_info=True)
