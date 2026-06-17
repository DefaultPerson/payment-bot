from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.core.i18n import JsonI18n


def build_start_text(i18n: JsonI18n, settings: Settings, language_code: str | None) -> str:
    language = language_code if language_code in settings.bot.supported_languages else None
    support_url = f"https://t.me/{settings.bot.support_username}"
    return i18n.gettext("start.text", language, support_url=support_url)


def create_start_router(i18n: JsonI18n, settings: Settings) -> Router:
    start_router = Router(name="start")

    @start_router.message(CommandStart())
    async def start(message: Message) -> None:
        language_code = message.from_user.language_code if message.from_user else None
        await message.answer(build_start_text(i18n, settings, language_code))

    return start_router
