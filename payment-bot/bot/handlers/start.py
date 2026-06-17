from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.menu import set_user_commands
from bot.keyboards import main_menu_keyboard
from bot.repos.users import upsert_user_from_telegram


@dataclass(frozen=True, slots=True)
class TelegramContext:
    tg_id: int | None
    chat_id: int | None
    username: str | None
    first_name: str | None
    language_code: str | None
    is_admin: bool


def build_start_text(i18n: JsonI18n, settings: Settings, language_code: str | None) -> str:
    language = i18n.normalize_language(language_code)
    if language not in settings.bot.supported_languages:
        language = None
    support_url = f"https://t.me/{settings.bot.support_username}"
    return i18n.gettext("start.text", language, support_url=support_url)


def message_context(message: Message, settings: Settings | None = None) -> TelegramContext:
    from_user = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    user_id = getattr(from_user, "id", None)
    chat_id = getattr(chat, "id", None)

    return TelegramContext(
        tg_id=user_id,
        chat_id=chat_id,
        username=getattr(from_user, "username", None),
        first_name=getattr(from_user, "first_name", None),
        language_code=getattr(from_user, "language_code", None),
        is_admin=user_id is not None and settings is not None and user_id in settings.admin_ids,
    )


def callback_context(callback: object, settings: Settings | None = None) -> TelegramContext:
    from_user = getattr(callback, "from_user", None)
    message = getattr(callback, "message", None)
    chat = getattr(message, "chat", None)
    user_id = getattr(from_user, "id", None)
    chat_id = getattr(chat, "id", None)

    return TelegramContext(
        tg_id=user_id,
        chat_id=chat_id,
        username=getattr(from_user, "username", None),
        first_name=getattr(from_user, "first_name", None),
        language_code=getattr(from_user, "language_code", None),
        is_admin=user_id is not None and settings is not None and user_id in settings.admin_ids,
    )


async def start(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    context = message_context(message, settings)
    lang_code = i18n.resolve_language(context.language_code)

    if context.tg_id is not None:
        async with session_scope(session_factory) as session:
            user = await upsert_user_from_telegram(
                session,
                tg_id=context.tg_id,
                username=context.username,
                first_name=context.first_name,
                language_code=context.language_code,
                i18n=i18n,
            )
            lang_code = user.lang_code

        if context.chat_id is not None:
            await set_user_commands(
                bot,
                i18n,
                settings,
                chat_id=context.chat_id,
                lang_code=lang_code,
                is_admin=context.is_admin,
            )

    await message.answer(
        build_start_text(i18n, settings, lang_code),
        reply_markup=main_menu_keyboard(i18n, lang_code, is_admin=context.is_admin),
    )


def create_start_router() -> Router:
    start_router = Router(name="start")
    start_router.message(CommandStart())(start)
    return start_router
