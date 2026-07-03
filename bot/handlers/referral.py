from __future__ import annotations

import logging
from decimal import Decimal

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
from bot.keyboards import CB_REFERRAL, close_keyboard
from bot.repos.referrals import ReferralStats, get_referral_stats
from bot.repos.users import ensure_minimal_user
from bot.services.message_ttl import schedule_deletion

logger = logging.getLogger(__name__)

CALLBACK_ANSWER_TEXT_LIMIT = 200


async def referral(
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
        text, lang_code = await _build_referral_response(
            context.tg_id,
            context.language_code,
            settings,
            i18n,
            session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to build referral response")
        await message.answer(i18n.gettext("error.generic", context.language_code))
        return
    sent = await message.answer(text, reply_markup=close_keyboard(i18n, lang_code))
    schedule_deletion(sent, delay_seconds=settings.bot.ephemeral_ttl_seconds)


async def referral_callback(
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
        text, _lang_code = await _build_referral_response(
            context.tg_id,
            context.language_code,
            settings,
            i18n,
            session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to build referral callback response")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return
    await _edit_text_or_answer(callback, text)


async def _build_referral_response(
    tg_id: int,
    language_code: str | None,
    settings: Settings,
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
        stats = await get_referral_stats(session, referrer_id=user.id)
        lang_code = user.lang_code
    return _format_referral_text(i18n, settings, lang_code, tg_id=tg_id, stats=stats), lang_code


def _format_referral_text(
    i18n: JsonI18n,
    settings: Settings,
    lang_code: str,
    *,
    tg_id: int,
    stats: ReferralStats,
) -> str:
    invite_link = f"https://t.me/{settings.bot.username}?start=ref_{tg_id}"
    return i18n.gettext(
        "referral.text",
        lang_code,
        invite_link=invite_link,
        referral_count=stats.referral_count,
        referral_earned_usd=_money(stats.earned_usd),
        referral_unpaid_earned_usd=_money(stats.available_usd),
        min_withdraw_usd=_money(settings.referral.min_withdraw_usd),
    )


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


async def _edit_text_or_answer(callback: CallbackQuery, text: str) -> None:
    edit_text = getattr(callback.message, "edit_text", None)
    if callable(edit_text):
        try:
            await edit_text(text)
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


def create_referral_router() -> Router:
    router = Router(name="referral")
    router.message(Command("referral"))(referral)
    router.callback_query(F.data == CB_REFERRAL)(referral_callback)
    return router
