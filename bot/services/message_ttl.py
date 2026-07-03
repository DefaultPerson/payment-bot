"""Best-effort, restart-durable auto-deletion of ephemeral chat messages.

Clutter messages (payment/trial confirmations, /referral, /promo, empty-access notice) get a
close (❌) button for immediate dismissal AND an optional auto-deletion after a TTL. The delete
job is a module-level, picklable APScheduler target so it survives a restart via the Redis-backed
jobstore (the same store scheduled broadcasts use); reloaded jobs cannot carry a pickled bot, so
they use the process-wide instance registered at startup.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

# Set once at startup. Delete jobs reloaded from Redis after a restart cannot carry a pickled
# bot/scheduler, so they use these process-wide instances instead.
_bot: Bot | None = None
_scheduler: AsyncIOScheduler | None = None


def configure_message_ttl(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    """Register the process bot + scheduler used to auto-delete ephemeral messages."""
    global _bot, _scheduler
    _bot = bot
    _scheduler = scheduler


async def delete_message_job(chat_id: int, message_id: int) -> None:
    """Module-level, picklable APScheduler target: delete one message, ignoring failures
    (already dismissed via the close button, or older than Telegram's 48h bot-delete window)."""
    bot = _bot
    if bot is None:  # pragma: no cover - defensive; bot is set at startup
        logger.error("message-ttl delete fired without a configured bot")
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        logger.debug("message-ttl: message %s/%s could not be deleted", chat_id, message_id)


def schedule_deletion(message: object | None, *, delay_seconds: int) -> None:
    """Schedule deletion of a sent/edited ``Message`` (guards missing chat/id). Convenience over
    :func:`schedule_message_deletion` for handler call sites."""
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(message, "message_id", None)
    if chat_id is None or message_id is None:
        return
    schedule_message_deletion(chat_id, message_id, delay_seconds=delay_seconds)


def schedule_message_deletion(chat_id: int, message_id: int, *, delay_seconds: int) -> None:
    """Schedule a one-off deletion of ``message_id`` after ``delay_seconds``.

    No-op when the scheduler is not configured or ``delay_seconds`` <= 0 (auto-delete disabled;
    the close button still works). Scheduling failures never propagate to the caller."""
    scheduler = _scheduler
    if scheduler is None or delay_seconds <= 0:
        return
    run_date = datetime.now(UTC) + timedelta(seconds=delay_seconds)
    try:
        scheduler.add_job(
            delete_message_job,
            trigger=DateTrigger(run_date),
            args=[chat_id, message_id],
            id=f"msgttl:{chat_id}:{message_id}",
            replace_existing=True,
            # Still fire (up to 1h late) if the run time fell during a restart.
            misfire_grace_time=3600,
        )
    except Exception:  # pragma: no cover - scheduling must never break a handler
        logger.warning("failed to schedule message deletion", exc_info=True)
