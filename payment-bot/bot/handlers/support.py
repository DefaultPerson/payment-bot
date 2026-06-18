from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.start import callback_context, message_context
from bot.keyboards import (
    CB_ADMIN,
    CB_ENTER_CHAT,
    CB_REFERRAL,
    CB_RENEW,
    CB_SUPPORT,
    support_keyboard,
)
from bot.repos.users import ensure_minimal_user, upsert_user_from_telegram

logger = logging.getLogger(__name__)

FUTURE_CALLBACKS = frozenset({CB_ADMIN, CB_REFERRAL, CB_RENEW, CB_ENTER_CHAT})


async def support(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = message_context(message, settings)
    try:
        lang_code = await _upsert_message_user_language(context, i18n, session_factory)
    except SQLAlchemyError:
        logger.exception("failed to resolve support command user")
        await message.answer(i18n.gettext("error.generic", context.language_code))
        return
    await message.answer(
        _support_text(i18n, settings, lang_code),
        reply_markup=support_keyboard(i18n, settings, lang_code),
    )


async def support_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = callback_context(callback, settings)
    try:
        lang_code = await _resolve_user_language(
            context.tg_id,
            context.language_code,
            i18n,
            session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to resolve support callback user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return
    await _edit_text_or_answer(
        callback,
        _support_text(i18n, settings, lang_code),
        reply_markup=support_keyboard(i18n, settings, lang_code),
    )


async def coming_soon_callback(callback: CallbackQuery, i18n: JsonI18n) -> None:
    from_user = getattr(callback, "from_user", None)
    lang_code = i18n.resolve_language(getattr(from_user, "language_code", None))
    await callback.answer(i18n.gettext("coming_soon.text", lang_code), show_alert=False)


async def _resolve_user_language(
    tg_id: int | None,
    language_code: str | None,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    if tg_id is None:
        return i18n.resolve_language(language_code)

    async with session_scope(session_factory) as session:
        user = await ensure_minimal_user(
            session,
            tg_id=tg_id,
            i18n=i18n,
            language_code=language_code,
        )
        return user.lang_code


async def _upsert_message_user_language(
    context: object,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    tg_id = getattr(context, "tg_id", None)
    language_code = getattr(context, "language_code", None)
    if tg_id is None:
        return i18n.resolve_language(language_code)

    async with session_scope(session_factory) as session:
        user = await upsert_user_from_telegram(
            session,
            tg_id=tg_id,
            username=getattr(context, "username", None),
            first_name=getattr(context, "first_name", None),
            language_code=language_code,
            i18n=i18n,
        )
        return user.lang_code


def _support_text(i18n: JsonI18n, settings: Settings, lang_code: str) -> str:
    return i18n.gettext(
        "support.text",
        lang_code,
        support_url=f"https://t.me/{settings.bot.support_username}",
    )


async def _edit_text_or_answer(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: object | None = None,
) -> None:
    edit_text = getattr(callback.message, "edit_text", None)
    if callable(edit_text):
        try:
            await edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            await callback.answer(text, show_alert=True)
        else:
            await callback.answer()
        return

    await callback.answer(text, show_alert=True)


def create_support_router() -> Router:
    router = Router(name="support")
    router.message(Command("support"))(support)
    router.callback_query(F.data == CB_SUPPORT)(support_callback)
    router.callback_query(F.data.in_(FUTURE_CALLBACKS))(coming_soon_callback)
    return router
