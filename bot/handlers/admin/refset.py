from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.admin._nav import callback_language, edit_or_answer, message_language
from bot.handlers.admin.states import AdminRefsetState
from bot.keyboards.admin import back_keyboard, cancel_keyboard
from bot.repos.referrals import set_custom_percent_for_referrer
from bot.repos.users import ensure_minimal_user

logger = logging.getLogger(__name__)


def parse_refset_args(text: str) -> tuple[int, Decimal]:
    parts = text.strip().split()
    if len(parts) != 3:
        raise ValueError

    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/refset":
        raise ValueError

    try:
        user_id = int(parts[1])
        percent = Decimal(parts[2])
    except (ValueError, InvalidOperation) as exc:
        raise ValueError from exc

    if user_id <= 0 or not percent.is_finite() or percent < 0 or percent >= 100:
        raise ValueError

    return user_id, percent


async def perform_refset(
    *,
    target_tg_id: int,
    percent: Decimal,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Set the custom referral percent for a referrer; return the count of updated links."""
    async with session_scope(session_factory) as session:
        referrer = await ensure_minimal_user(session, tg_id=target_tg_id, i18n=i18n)
        return await set_custom_percent_for_referrer(
            session,
            referrer=referrer,
            percent=percent,
        )


async def refset_start(
    callback: CallbackQuery,
    settings: Settings,
    i18n: JsonI18n,
    state: FSMContext,
) -> None:
    user = callback.from_user
    if user is None or user.id not in settings.admin_ids:
        await callback.answer()
        return
    lang = callback_language(callback, i18n)
    await state.set_state(AdminRefsetState.waiting_input)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.refset.prompt", lang),
        reply_markup=cancel_keyboard(i18n, lang),
    )


async def refset_input(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    lang = message_language(message, i18n)
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        return

    try:
        target_tg_id, percent = parse_refset_args(f"/refset {message.text or ''}")
    except ValueError:
        await message.answer(
            i18n.gettext("admin.refset.invalid", lang),
            reply_markup=cancel_keyboard(i18n, lang),
        )
        return

    try:
        updated = await perform_refset(
            target_tg_id=target_tg_id,
            percent=percent,
            i18n=i18n,
            session_factory=session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to set referral percent")
        await state.set_state(None)
        await message.answer(
            i18n.gettext("error.generic", lang),
            reply_markup=back_keyboard(i18n, lang),
        )
        return

    await state.set_state(None)
    key = "refset.success" if updated else "refset.success.future"
    await message.answer(
        i18n.gettext(
            key,
            lang,
            user_id=target_tg_id,
            percent=_format_percent(percent),
            updated=updated,
        ),
        reply_markup=back_keyboard(i18n, lang),
    )


def _format_percent(percent: Decimal) -> str:
    formatted = format(percent.normalize(), "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
