from __future__ import annotations

import logging
from datetime import UTC, datetime

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
from bot.keyboards import CB_PROFILE, profile_keyboard
from bot.repos.referrals import get_referral_stats
from bot.repos.subscriptions import get_subscription_for_user
from bot.repos.users import ensure_minimal_user
from bot.services.profile import build_profile_view, format_profile_text

logger = logging.getLogger(__name__)

CALLBACK_ANSWER_TEXT_LIMIT = 200


async def profile(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = message_context(message, settings)
    if context.tg_id is None:
        await message.answer(i18n.gettext("error.generic", context.language_code))
        return

    try:
        text, lang_code = await _build_profile_response(
            context.tg_id,
            context.language_code,
            i18n,
            session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to build profile response")
        await message.answer(i18n.gettext("error.generic", context.language_code))
        return
    await message.answer(text, reply_markup=profile_keyboard(i18n, settings, lang_code))


async def profile_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = callback_context(callback, settings)
    if context.tg_id is None:
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    try:
        text, lang_code = await _build_profile_response(
            context.tg_id,
            context.language_code,
            i18n,
            session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to build profile callback response")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return
    await _edit_text_or_answer(
        callback,
        text,
        reply_markup=profile_keyboard(i18n, settings, lang_code),
    )


async def _build_profile_response(
    tg_id: int,
    language_code: str | None,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    async with session_scope(session_factory) as session:
        user = await ensure_minimal_user(
            session,
            tg_id=tg_id,
            i18n=i18n,
            language_code=language_code,
        )
        subscription = await get_subscription_for_user(session, user.id)
        referral_stats = await get_referral_stats(session, referrer_id=user.id)
        view = build_profile_view(
            user,
            subscription,
            now=datetime.now(UTC),
            referral_stats=referral_stats,
        )
        lang_code = user.lang_code

    return format_profile_text(view, i18n, lang_code), lang_code


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
            await callback.answer(_callback_answer_text(text), show_alert=True)
        else:
            await callback.answer()
        return

    await callback.answer(_callback_answer_text(text), show_alert=True)


def _callback_answer_text(text: str) -> str:
    if len(text) <= CALLBACK_ANSWER_TEXT_LIMIT:
        return text

    ellipsis = "..."
    return f"{text[: CALLBACK_ANSWER_TEXT_LIMIT - len(ellipsis)]}{ellipsis}"


def create_profile_router() -> Router:
    router = Router(name="profile")
    router.message(Command("profile"))(profile)
    router.callback_query(F.data == CB_PROFILE)(profile_callback)
    return router
