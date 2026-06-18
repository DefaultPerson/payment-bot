from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
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


async def refset(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    language = _message_language(message, i18n)
    admin_id = _message_user_id(message)
    if admin_id is None or admin_id not in settings.admin_ids:
        await message.answer(i18n.gettext("refset.forbidden", language))
        return

    try:
        target_tg_id, percent = parse_refset_args(message.text or "")
    except ValueError:
        await message.answer(i18n.gettext("refset.usage", language))
        return

    try:
        async with session_scope(session_factory) as session:
            referrer = await ensure_minimal_user(session, tg_id=target_tg_id, i18n=i18n)
            updated = await set_custom_percent_for_referrer(
                session,
                referrer=referrer,
                percent=percent,
            )
    except SQLAlchemyError:
        logger.exception("failed to set referral percent")
        await message.answer(i18n.gettext("error.generic", language))
        return

    key = "refset.success" if updated else "refset.success.future"
    await message.answer(
        i18n.gettext(
            key,
            language,
            user_id=target_tg_id,
            percent=_format_percent(percent),
            updated=updated,
        ),
    )


async def refset_forbidden(message: Message, i18n: JsonI18n) -> None:
    language = _message_language(message, i18n)
    await message.answer(i18n.gettext("refset.forbidden", language))


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user else None


def _message_language(message: Message, i18n: JsonI18n) -> str:
    user = message.from_user
    language_code = user.language_code if user else None
    return i18n.resolve_language(language_code)


def _format_percent(percent: Decimal) -> str:
    formatted = format(percent.normalize(), "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
