from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

from bot.config import Settings
from bot.core.i18n import JsonI18n

logger = logging.getLogger(__name__)

USER_COMMANDS = ("start", "profile", "language", "support")
ADMIN_COMMANDS = ("grant", "broadcast")
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
        await bot.set_my_commands(
            build_commands(i18n, language, is_admin=is_admin),
            scope=BotCommandScopeChat(chat_id=chat_id),
            language_code=TELEGRAM_LANGUAGE_CODES.get(language, language),
        )
    except Exception:
        logger.warning("failed to set bot commands for chat_id=%s", chat_id, exc_info=True)
