from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.core.i18n import JsonI18n


def build_start_text(i18n: JsonI18n, settings: Settings, language_code: str | None) -> str:
    language = i18n.normalize_language(language_code)
    if language not in settings.bot.supported_languages:
        language = None
    support_url = f"https://t.me/{settings.bot.support_username}"
    return i18n.gettext("start.text", language, support_url=support_url)


async def start(message: Message, settings: Settings, i18n: JsonI18n) -> None:
    language_code = message.from_user.language_code if message.from_user else None
    await message.answer(build_start_text(i18n, settings, language_code))


def create_start_router() -> Router:
    start_router = Router(name="start")
    start_router.message(CommandStart())(start)
    return start_router
