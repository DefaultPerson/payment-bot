from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.admin._nav import callback_language, edit_or_answer, message_language
from bot.handlers.admin.states import AdminRefResetState
from bot.keyboards.admin import back_keyboard, cancel_keyboard
from bot.repos.referrals import reset_referral_balance
from bot.repos.users import ensure_minimal_user

logger = logging.getLogger(__name__)


def parse_refreset_args(text: str) -> int:
    parts = text.strip().split()
    if len(parts) != 2:
        raise ValueError

    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/refreset":
        raise ValueError

    try:
        user_id = int(parts[1])
    except ValueError as exc:
        raise ValueError from exc

    if user_id <= 0:
        raise ValueError

    return user_id


async def perform_refreset(
    *,
    target_tg_id: int,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Zero the user's referral balance; return the count of earnings settled."""
    async with session_scope(session_factory) as session:
        referrer = await ensure_minimal_user(session, tg_id=target_tg_id, i18n=i18n)
        return await reset_referral_balance(session, referrer=referrer, now=datetime.now(UTC))


async def refreset_start(
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
    await state.set_state(AdminRefResetState.waiting_input)
    await edit_or_answer(
        callback,
        i18n.gettext("admin.refreset.prompt", lang),
        reply_markup=cancel_keyboard(i18n, lang),
    )


async def refreset_input(
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
        target_tg_id = parse_refreset_args(f"/refreset {message.text or ''}")
    except ValueError:
        await message.answer(
            i18n.gettext("admin.refreset.invalid", lang),
            reply_markup=cancel_keyboard(i18n, lang),
        )
        return

    try:
        cleared = await perform_refreset(
            target_tg_id=target_tg_id,
            i18n=i18n,
            session_factory=session_factory,
        )
    except SQLAlchemyError:
        logger.exception("failed to reset referral balance")
        await state.set_state(None)
        await message.answer(
            i18n.gettext("error.generic", lang),
            reply_markup=back_keyboard(i18n, lang),
        )
        return

    await state.set_state(None)
    await message.answer(
        i18n.gettext("admin.refreset.success", lang, user_id=target_tg_id, cleared=cleared),
        reply_markup=back_keyboard(i18n, lang),
    )
