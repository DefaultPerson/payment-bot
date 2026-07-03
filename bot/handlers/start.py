from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.menu import set_user_commands
from bot.core.text_emoji import text_emoji
from bot.keyboards import CB_DISMISS, CB_MENU, main_menu_keyboard
from bot.repos.subscriptions import get_subscription_for_user
from bot.repos.users import ensure_minimal_user, upsert_user_from_telegram_with_created
from bot.services.profile import profile_plan_label
from bot.services.referrals import (
    ReferralBindResult,
    ReferralBindStatus,
    bind_referral_from_start,
    parse_referral_payload,
)
from bot.services.trial import is_trial_available

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramContext:
    tg_id: int | None
    chat_id: int | None
    username: str | None
    first_name: str | None
    language_code: str | None
    is_admin: bool


def build_start_text(
    i18n: JsonI18n,
    settings: Settings,
    language_code: str | None,
    *,
    status_label: str,
    trial_available: bool = False,
) -> str:
    language = i18n.normalize_language(language_code)
    if language not in settings.bot.supported_languages:
        language = None
    trial_block = ""
    if trial_available:
        cta = i18n.gettext(
            "start.trial.cta",
            language,
            new=text_emoji("new"),
            days=settings.trial.duration_days,
        )
        trial_block = f"\n\n{cta}"
    return i18n.gettext(
        "start.text",
        language,
        wave=text_emoji("wave"),
        api=text_emoji("api"),
        question=text_emoji("question"),
        trial_block=trial_block,
        status=status_label,
        support=f"@{settings.bot.support_username}",
        docs_url=settings.bot.docs_url,
    )


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
    state: FSMContext | None = None,
) -> None:
    # /start is the universal reset: drop any in-flight FSM (e.g. the inline promo prompt)
    # so a stale state can't swallow the user's next message.
    if state is not None:
        await state.clear()
    context = message_context(message, settings)
    lang_code = i18n.resolve_language(context.language_code)
    referral_result: ReferralBindResult | None = None
    trial_available = False
    now = datetime.now(UTC)
    status_label = i18n.gettext("start.status.free", lang_code)

    if context.tg_id is not None:
        try:
            async with session_scope(session_factory) as session:
                upserted_user = await upsert_user_from_telegram_with_created(
                    session,
                    tg_id=context.tg_id,
                    username=context.username,
                    first_name=context.first_name,
                    language_code=context.language_code,
                    i18n=i18n,
                )
                user = upserted_user.user
                lang_code = user.lang_code
                referrer_tg_id = parse_referral_payload(getattr(message, "text", None))
                if referrer_tg_id is not None and upserted_user.created:
                    referral_result = await bind_referral_from_start(
                        session,
                        referee=user,
                        referrer_tg_id=referrer_tg_id,
                        now=now,
                        allow_referrer_change=settings.referral.allow_referrer_change,
                    )
                subscription = await get_subscription_for_user(session, user.id)
                trial_available = await is_trial_available(
                    session, user=user, settings=settings, now=now
                )
                status_label = profile_plan_label(
                    settings, i18n, lang_code, subscription, now
                )
        except SQLAlchemyError:
            logger.exception("failed to upsert user on start")
            await message.answer(i18n.gettext("error.generic", lang_code))
            return

        if context.chat_id is not None:
            await set_user_commands(
                bot,
                i18n,
                settings,
                chat_id=context.chat_id,
                lang_code=lang_code,
                is_admin=context.is_admin,
            )

    text = _start_text_with_referral_feedback(
        i18n,
        settings,
        lang_code,
        referral_result,
        status_label=status_label,
        trial_available=trial_available,
    )
    await message.answer(
        text,
        reply_markup=main_menu_keyboard(
            i18n, lang_code, settings, is_admin=context.is_admin, show_trial=trial_available
        ),
    )


def _start_text_with_referral_feedback(
    i18n: JsonI18n,
    settings: Settings,
    lang_code: str,
    referral_result: ReferralBindResult | None,
    *,
    status_label: str,
    trial_available: bool,
) -> str:
    text = build_start_text(
        i18n, settings, lang_code, status_label=status_label, trial_available=trial_available
    )
    if referral_result is None:
        return text
    if referral_result.status is ReferralBindStatus.applied:
        return f"{text}\n\n{i18n.gettext('referral.start.applied', lang_code)}"
    if referral_result.status is ReferralBindStatus.rebind_rejected:
        return f"{text}\n\n{i18n.gettext('referral.start.rebind_rejected', lang_code)}"
    return text


async def back_to_menu_callback(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Re-render the main menu (e.g. the language menu's "Back" button)."""
    context = callback_context(callback, settings)
    lang_code = i18n.resolve_language(context.language_code)
    now = datetime.now(UTC)
    trial_available = False
    status_label = i18n.gettext("start.status.free", lang_code)

    if context.tg_id is not None:
        try:
            async with session_scope(session_factory) as session:
                user = await ensure_minimal_user(
                    session,
                    tg_id=context.tg_id,
                    i18n=i18n,
                    language_code=context.language_code,
                )
                lang_code = user.lang_code
                subscription = await get_subscription_for_user(session, user.id)
                trial_available = await is_trial_available(
                    session, user=user, settings=settings, now=now
                )
                status_label = profile_plan_label(
                    settings, i18n, lang_code, subscription, now
                )
        except SQLAlchemyError:
            logger.exception("failed to render main menu")
            await callback.answer(
                i18n.gettext("error.generic", context.language_code), show_alert=True
            )
            return

    text = build_start_text(
        i18n, settings, lang_code, status_label=status_label, trial_available=trial_available
    )
    await _edit_menu_or_answer(
        callback,
        text,
        reply_markup=main_menu_keyboard(
            i18n, lang_code, settings, is_admin=context.is_admin, show_trial=trial_available
        ),
    )


async def dismiss_callback(callback: CallbackQuery) -> None:
    """Delete the message the button is attached to (e.g. the one-time access-links message)."""
    delete = getattr(callback.message, "delete", None)
    if callable(delete):
        try:
            await delete()
        except TelegramBadRequest:
            # Already deleted, or older than Telegram's 48h bot-deletion window.
            logger.debug("dismiss: message could not be deleted")
    await callback.answer()


async def _edit_menu_or_answer(
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
            await callback.answer()
        else:
            await callback.answer()
        return
    await callback.answer()


def create_start_router() -> Router:
    start_router = Router(name="start")
    start_router.message(CommandStart())(start)
    start_router.callback_query(F.data == CB_MENU)(back_to_menu_callback)
    start_router.callback_query(F.data == CB_DISMISS)(dismiss_callback)
    return start_router
