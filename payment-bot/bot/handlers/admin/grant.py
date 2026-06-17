from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.repos.subscriptions import extend_manual_subscription
from bot.repos.users import ensure_minimal_user

logger = logging.getLogger(__name__)


def parse_grant_args(text: str) -> tuple[int, int]:
    parts = text.strip().split()
    if len(parts) != 3:
        raise ValueError

    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/grant":
        raise ValueError

    try:
        user_id = int(parts[1])
        days = int(parts[2])
    except ValueError as exc:
        raise ValueError from exc

    if user_id <= 0 or days <= 0:
        raise ValueError

    return user_id, days


async def grant(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    service_channel: ServiceChannel,
) -> None:
    language = _message_language(message, i18n)
    admin_id = _message_user_id(message)
    if admin_id is None or admin_id not in settings.admin_ids:
        await message.answer(i18n.gettext("grant.forbidden", language))
        return

    try:
        target_tg_id, days = parse_grant_args(message.text or "")
    except ValueError:
        await message.answer(i18n.gettext("grant.usage", language))
        return

    now = datetime.now(UTC)
    try:
        async with session_scope(session_factory) as session:
            user = await ensure_minimal_user(session, tg_id=target_tg_id, i18n=i18n)
            subscription = await extend_manual_subscription(
                session,
                user=user,
                days=days,
                now=now,
            )
            expires_at = subscription.expires_at
    except SQLAlchemyError:
        logger.exception("failed to apply manual grant")
        await message.answer(i18n.gettext("error.generic", language))
        return

    await _notify_service_channel(
        service_channel,
        admin_id=admin_id,
        target_tg_id=target_tg_id,
        days=days,
        expires_at=expires_at,
    )

    success_text = i18n.gettext(
        "grant.success",
        language,
        user_id=target_tg_id,
        days=days,
    )
    await message.answer(f"{success_text} Expires at {_format_expiry(expires_at)}")


async def grant_forbidden(message: Message, i18n: JsonI18n) -> None:
    language = _message_language(message, i18n)
    await message.answer(i18n.gettext("grant.forbidden", language))


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user else None


def _message_language(message: Message, i18n: JsonI18n) -> str:
    user = message.from_user
    language_code = user.language_code if user else None
    return i18n.resolve_language(language_code)


async def _notify_service_channel(
    service_channel: ServiceChannel,
    *,
    admin_id: int,
    target_tg_id: int,
    days: int,
    expires_at: datetime,
) -> None:
    text = (
        "<b>admin manual grant</b>\n"
        f"admin_id={admin_id}\n"
        f"target_tg_id={target_tg_id}\n"
        f"days={days}\n"
        f"expires_at={_format_expiry(expires_at)}"
    )
    try:
        await service_channel.send_html(text)
    except Exception:
        return


def _format_expiry(expires_at: datetime) -> str:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    expires_at = expires_at.astimezone(UTC)
    return expires_at.strftime("%Y-%m-%d %H:%M UTC")
