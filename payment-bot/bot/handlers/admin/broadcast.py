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
from bot.core.metrics import PaymentBotMetrics
from bot.repos.broadcasts import (
    BroadcastSegment,
    create_broadcast_log,
    finish_broadcast_log_by_id,
    get_broadcast_targets,
)
from bot.repos.users import ensure_minimal_user
from bot.services.broadcasts import SupportsBroadcastBot, run_text_broadcast

logger = logging.getLogger(__name__)

_VALID_SEGMENTS: set[BroadcastSegment] = {"all", "active", "inactive"}


def parse_broadcast_args(text: str) -> tuple[BroadcastSegment, str]:
    parts = text.strip().split(maxsplit=2)
    if len(parts) != 3:
        raise ValueError

    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/broadcast":
        raise ValueError

    segment = parts[1]
    broadcast_text = parts[2].strip()
    if segment not in _VALID_SEGMENTS or not broadcast_text:
        raise ValueError

    return segment, broadcast_text


async def broadcast(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    bot: SupportsBroadcastBot,
    service_channel: ServiceChannel,
    metrics: PaymentBotMetrics | None = None,
) -> None:
    language = _message_language(message, i18n)
    admin_tg_id = _message_user_id(message)
    if admin_tg_id is None or admin_tg_id not in settings.admin_ids:
        await message.answer(i18n.gettext("broadcast.forbidden", language))
        return

    try:
        segment, broadcast_text = parse_broadcast_args(message.text or "")
    except ValueError:
        await message.answer(i18n.gettext("broadcast.usage", language))
        return

    started_at = datetime.now(UTC)
    try:
        async with session_scope(session_factory) as session:
            admin_user = await ensure_minimal_user(
                session,
                tg_id=admin_tg_id,
                i18n=i18n,
                language_code=_message_language_code(message),
            )
            targets = await get_broadcast_targets(session, segment=segment, now=started_at)
            log = await create_broadcast_log(
                session,
                admin_user_id=admin_user.id,
                message_text=broadcast_text,
                target_filter={"segment": segment},
                started_at=started_at,
            )
            log_id = log.id
    except SQLAlchemyError:
        logger.exception("failed to prepare admin broadcast")
        await message.answer(i18n.gettext("error.generic", language))
        return

    if log_id is None:
        logger.error("broadcast log did not receive an id")
        await message.answer(i18n.gettext("error.generic", language))
        return

    try:
        result = await run_text_broadcast(bot, targets=targets, text=broadcast_text)
    except Exception:
        logger.exception("failed to send admin broadcast")
        if metrics is not None:
            metrics.record_broadcast(result="failed", count=len(targets))
        await _finish_failed_broadcast(
            session_factory,
            log_id=log_id,
            target_count=len(targets),
        )
        await message.answer(i18n.gettext("error.generic", language))
        return

    if metrics is not None:
        if result.successful:
            metrics.record_broadcast(result="success", count=result.successful)
        if result.failed:
            metrics.record_broadcast(result="failed", count=result.failed)

    try:
        await _finish_successful_broadcast(
            session_factory,
            log_id=log_id,
            total_count=result.total,
            success_count=result.successful,
            failed_count=result.failed,
        )
    except (LookupError, SQLAlchemyError):
        logger.exception("failed to finish admin broadcast log")
        await message.answer(i18n.gettext("error.generic", language))
        return

    await _notify_service_channel(
        service_channel,
        admin_tg_id=admin_tg_id,
        segment=segment,
        result_total=result.total,
        result_successful=result.successful,
        result_failed=result.failed,
    )
    await message.answer(
        i18n.gettext(
            "broadcast.success",
            language,
            segment=segment,
            total=result.total,
            successful=result.successful,
            failed=result.failed,
        ),
    )


async def broadcast_forbidden(message: Message, i18n: JsonI18n) -> None:
    language = _message_language(message, i18n)
    await message.answer(i18n.gettext("broadcast.forbidden", language))


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user else None


def _message_language(message: Message, i18n: JsonI18n) -> str:
    return i18n.resolve_language(_message_language_code(message))


def _message_language_code(message: Message) -> str | None:
    user = message.from_user
    return user.language_code if user else None


async def _notify_service_channel(
    service_channel: ServiceChannel,
    *,
    admin_tg_id: int,
    segment: str,
    result_total: int,
    result_successful: int,
    result_failed: int,
) -> None:
    text = (
        "<b>broadcast finished</b>\n"
        f"admin_id={admin_tg_id}\n"
        f"segment={segment}\n"
        f"sent={result_successful}/{result_total}\n"
        f"failed={result_failed}"
    )
    try:
        await service_channel.send_html(text)
    except Exception:
        return


async def _finish_successful_broadcast(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    log_id: int,
    total_count: int,
    success_count: int,
    failed_count: int,
) -> None:
    async with session_scope(session_factory) as session:
        await finish_broadcast_log_by_id(
            session,
            log_id,
            total_count=total_count,
            success_count=success_count,
            failed_count=failed_count,
            finished_at=datetime.now(UTC),
        )


async def _finish_failed_broadcast(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    log_id: int,
    target_count: int,
) -> None:
    try:
        await _finish_successful_broadcast(
            session_factory,
            log_id=log_id,
            total_count=target_count,
            success_count=0,
            failed_count=target_count,
        )
    except Exception:
        logger.exception("failed to mark admin broadcast as failed")
