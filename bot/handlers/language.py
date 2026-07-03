from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.menu import set_user_commands
from bot.handlers.start import build_start_text, callback_context, message_context
from bot.keyboards import CB_LANGUAGE, LANG_PREFIX, language_keyboard, main_menu_keyboard
from bot.repos.subscriptions import get_subscription_for_user
from bot.repos.users import ensure_minimal_user, update_user_language, upsert_user_from_telegram
from bot.services.profile import profile_plan_label
from bot.services.trial import is_trial_available

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGE_CALLBACKS = frozenset({"ru", "ua", "en"})


async def language(
    message: Message,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = message_context(message)
    try:
        lang_code = await _upsert_message_user_language(context, i18n, session_factory)
    except SQLAlchemyError:
        logger.exception("failed to resolve language command user")
        await message.answer(i18n.gettext("error.generic", context.language_code))
        return
    await message.answer(
        i18n.gettext("commands.language", lang_code),
        reply_markup=language_keyboard(i18n, lang_code),
    )


async def language_menu_callback(
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
        logger.exception("failed to resolve language menu callback user")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return
    await _edit_text_or_answer(
        callback,
        i18n.gettext("commands.language", lang_code),
        reply_markup=language_keyboard(i18n, lang_code),
    )


async def language_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    context = callback_context(callback, settings)
    lang_code = _parse_language_callback(getattr(callback, "data", None))
    if lang_code is None or context.tg_id is None:
        await callback.answer(
            i18n.gettext("error.generic", context.language_code),
            show_alert=True,
        )
        return

    trial_available = False
    now = datetime.now(UTC)
    status_label = i18n.gettext("start.status.free", lang_code)
    try:
        async with session_scope(session_factory) as session:
            await ensure_minimal_user(
                session,
                tg_id=context.tg_id,
                i18n=i18n,
                language_code=context.language_code,
            )
            user = await update_user_language(session, tg_id=context.tg_id, lang_code=lang_code)
            persisted_lang_code = user.lang_code
            subscription = await get_subscription_for_user(session, user.id)
            trial_available = await is_trial_available(
                session, user=user, settings=settings, now=now
            )
            status_label = profile_plan_label(
                settings, i18n, persisted_lang_code, subscription, now
            )
    except SQLAlchemyError:
        logger.exception("failed to update language")
        await callback.answer(i18n.gettext("error.generic", context.language_code), show_alert=True)
        return

    if context.chat_id is not None:
        await set_user_commands(
            bot,
            i18n,
            settings,
            chat_id=context.chat_id,
            lang_code=persisted_lang_code,
            is_admin=context.is_admin,
        )

    language_name = i18n.gettext(f"language.name.{persisted_lang_code}", persisted_lang_code)
    changed_text = i18n.gettext(
        "language.changed",
        persisted_lang_code,
        language_name=language_name,
    )
    await _edit_text_or_answer(
        callback,
        build_start_text(
            i18n,
            settings,
            persisted_lang_code,
            status_label=status_label,
            trial_available=trial_available,
        ),
        answer_text=changed_text,
        reply_markup=main_menu_keyboard(
            i18n,
            persisted_lang_code,
            settings,
            is_admin=context.is_admin,
            show_trial=trial_available,
        ),
    )


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


def _parse_language_callback(data: str | None) -> str | None:
    if data is None or not data.startswith(LANG_PREFIX):
        return None
    lang_code = data.removeprefix(LANG_PREFIX)
    if lang_code not in SUPPORTED_LANGUAGE_CALLBACKS:
        return None
    return lang_code


async def _edit_text_or_answer(
    callback: CallbackQuery,
    text: str,
    *,
    answer_text: str | None = None,
    reply_markup: object | None = None,
) -> None:
    edit_text = getattr(callback.message, "edit_text", None)
    if callable(edit_text):
        try:
            await edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            await callback.answer(text, show_alert=True)
        else:
            await callback.answer(answer_text)
        return

    await callback.answer(text, show_alert=True)


def create_language_router() -> Router:
    router = Router(name="language")
    router.message(Command("language"))(language)
    router.callback_query(F.data == CB_LANGUAGE)(language_menu_callback)
    router.callback_query(F.data.startswith(LANG_PREFIX))(language_callback)
    return router
