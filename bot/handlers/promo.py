from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.handlers.start import message_context
from bot.keyboards import CB_PROMO
from bot.repos.promos import PromoUnavailable, apply_promo_code_for_user

logger = logging.getLogger(__name__)


def parse_promo_args(text: str | None) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError
    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/promo":
        raise ValueError
    code = parts[1].strip()
    if not code:
        raise ValueError
    return code


async def promo_command(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del settings
    context = message_context(message)
    lang_code = i18n.resolve_language(context.language_code)
    if context.tg_id is None:
        await message.answer(i18n.gettext("error.generic", lang_code))
        return

    try:
        code = parse_promo_args(message.text)
    except ValueError:
        await message.answer(i18n.gettext("promo.usage", lang_code))
        return

    try:
        async with session_scope(session_factory) as session:
            promo = await apply_promo_code_for_user(
                session,
                tg_id=context.tg_id,
                code=code,
                language_code=context.language_code,
                i18n=i18n,
            )
            applied_code = promo.code
        await message.answer(i18n.gettext("promo.applied", lang_code, code=applied_code))
    except PromoUnavailable as exc:
        await message.answer(_promo_error_text(i18n, lang_code, exc.reason))
    except SQLAlchemyError:
        logger.exception("failed to apply promo code")
        await message.answer(i18n.gettext("error.generic", lang_code))


async def promo_callback(callback: CallbackQuery, i18n: JsonI18n) -> None:
    from_user = getattr(callback, "from_user", None)
    lang_code = i18n.resolve_language(getattr(from_user, "language_code", None))
    await callback.answer(i18n.gettext("promo.prompt", lang_code), show_alert=True)


def _promo_error_text(i18n: JsonI18n, lang_code: str, reason: str) -> str:
    key = f"promo.error.{reason}"
    text = i18n.gettext(key, lang_code)
    if text == key:
        return i18n.gettext("promo.error.generic", lang_code)
    return text


def create_promo_router() -> Router:
    router = Router(name="promo")
    router.message(Command("promo"))(promo_command)
    router.callback_query(F.data == CB_PROMO)(promo_callback)
    return router
