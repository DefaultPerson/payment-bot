from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.service_events import render_event
from bot.keyboards import profile_keyboard
from bot.models import Subscription, User
from bot.repos.subscriptions import (
    get_subscriptions_due_0d_notification,
    get_subscriptions_due_3d_notification,
    get_subscriptions_due_access_revoke,
    get_subscriptions_due_post_notification,
    mark_subscription_0d_notified,
    mark_subscription_3d_notified,
    mark_subscription_access_revoked,
    mark_subscription_post_notified,
)
from bot.services.access import revoke_protected_chat_access
from bot.services.notifications import format_subscription_notification

logger = logging.getLogger(__name__)

# The run summary is posted only once per process start (reset on restart). The job
# runs on a short interval, so a message every run would be channel noise.
_run_event_sent = False


def reset_run_event_state() -> None:
    """Clear the once-per-start guard (test hook)."""
    global _run_event_sent
    _run_event_sent = False


class SupportsNotificationBot(Protocol):
    async def send_message(self, **kwargs: object) -> object: ...

    async def ban_chat_member(self, **kwargs: object) -> object: ...

    async def unban_chat_member(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class NotificationRunResult:
    three_day_sent: int = 0
    zero_day_sent: int = 0
    post_sent: int = 0
    revoked: int = 0
    errors: int = 0

    def with_error(self) -> NotificationRunResult:
        return NotificationRunResult(
            three_day_sent=self.three_day_sent,
            zero_day_sent=self.zero_day_sent,
            post_sent=self.post_sent,
            revoked=self.revoked,
            errors=self.errors + 1,
        )

    def is_empty(self) -> bool:
        """True when the run did nothing — no notifications, no revokes, no errors."""
        return not any(
            (self.three_day_sent, self.zero_day_sent, self.post_sent, self.revoked, self.errors)
        )


async def notify_subscriptions_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: SupportsNotificationBot,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    now: datetime | None = None,
) -> NotificationRunResult:
    now = now or datetime.now(UTC)
    try:
        async with session_scope(session_factory) as session:
            result = await _notify_subscriptions_in_session(
                session,
                settings=settings,
                bot=bot,
                i18n=i18n,
                now=now,
            )
    except SQLAlchemyError:
        logger.exception("failed to process subscription notifications")
        result = NotificationRunResult(errors=1)

    await _notify_service_channel(service_channel, result)
    return result


async def _notify_subscriptions_in_session(
    session: AsyncSession,
    *,
    settings: Settings,
    bot: SupportsNotificationBot,
    i18n: JsonI18n,
    now: datetime,
) -> NotificationRunResult:
    errors = 0
    three_day_sent = await _process_notification_batch(
        session,
        await get_subscriptions_due_3d_notification(
            session,
            now=now,
            days_before=settings.notifications.reminder_days_before,
            window_seconds=settings.notifications.reminder_window_seconds,
            limit=settings.notifications.batch_size,
        ),
        kind="3d",
        marker=mark_subscription_3d_notified,
        settings=settings,
        bot=bot,
        i18n=i18n,
        now=now,
    )
    errors += three_day_sent.errors

    zero_day_sent = await _process_notification_batch(
        session,
        await get_subscriptions_due_0d_notification(
            session,
            now=now,
            limit=settings.notifications.batch_size,
        ),
        kind="0d",
        marker=mark_subscription_0d_notified,
        settings=settings,
        bot=bot,
        i18n=i18n,
        now=now,
    )
    errors += zero_day_sent.errors

    post_sent = await _process_notification_batch(
        session,
        await get_subscriptions_due_post_notification(
            session,
            now=now,
            delay_hours=settings.notifications.post_expired_delay_hours,
            limit=settings.notifications.batch_size,
        ),
        kind="post",
        marker=mark_subscription_post_notified,
        settings=settings,
        bot=bot,
        i18n=i18n,
        now=now,
    )
    errors += post_sent.errors

    revoke_result = await _process_revoke_batch(
        session,
        await get_subscriptions_due_access_revoke(
            session,
            now=now,
            delay_hours=settings.notifications.post_expired_delay_hours,
            limit=settings.notifications.batch_size,
        ),
        settings=settings,
        bot=bot,
        now=now,
    )
    errors += revoke_result.errors

    return NotificationRunResult(
        three_day_sent=three_day_sent.sent,
        zero_day_sent=zero_day_sent.sent,
        post_sent=post_sent.sent,
        revoked=revoke_result.revoked,
        errors=errors,
    )


@dataclass(frozen=True, slots=True)
class _NotificationBatchResult:
    sent: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class _RevokeBatchResult:
    revoked: int = 0
    errors: int = 0


async def _process_notification_batch(
    session: AsyncSession,
    subscriptions: list[Subscription],
    *,
    kind: str,
    marker,
    settings: Settings,
    bot: SupportsNotificationBot,
    i18n: JsonI18n,
    now: datetime,
) -> _NotificationBatchResult:
    sent = 0
    errors = 0
    for subscription in subscriptions:
        user = await _load_user(session, subscription.user_id)
        if user is None:
            errors += 1
            continue
        try:
            await bot.send_message(
                chat_id=user.tg_id,
                text=format_subscription_notification(
                    i18n,
                    kind,
                    user.lang_code,
                    expires_at=subscription.expires_at,
                    support_url=_support_url(settings),
                ),
                reply_markup=profile_keyboard(i18n, settings, user.lang_code),
            )
        except Exception:
            logger.exception(
                "failed to send subscription notification",
                extra={"user_id": user.id, "tg_id": user.tg_id, "kind": kind},
            )
            errors += 1
            continue

        await marker(session, subscription, notified_at=now)
        sent += 1
    return _NotificationBatchResult(sent=sent, errors=errors)


async def _process_revoke_batch(
    session: AsyncSession,
    subscriptions: list[Subscription],
    *,
    settings: Settings,
    bot: SupportsNotificationBot,
    now: datetime,
) -> _RevokeBatchResult:
    revoked = 0
    errors = 0
    for subscription in subscriptions:
        user = await _load_user(session, subscription.user_id)
        if user is None:
            errors += 1
            continue
        try:
            await revoke_protected_chat_access(bot, settings, tg_id=user.tg_id, now=now)
        except Exception:
            logger.exception(
                "failed to revoke subscription access",
                extra={"user_id": user.id, "tg_id": user.tg_id},
            )
            errors += 1
            continue

        await mark_subscription_access_revoked(session, subscription, revoked_at=now)
        revoked += 1
    return _RevokeBatchResult(revoked=revoked, errors=errors)


async def notification_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: SupportsNotificationBot,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    sleep: Callable[[int], Awaitable[None]] = asyncio.sleep,
    max_iterations: int | None = None,
) -> None:
    iterations = 0
    while True:
        try:
            await notify_subscriptions_once(
                session_factory=session_factory,
                settings=settings,
                bot=bot,
                i18n=i18n,
                service_channel=service_channel,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("subscription notification iteration failed")

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        await sleep(settings.notifications.loop_interval_seconds)


async def _load_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.scalar(select(User).where(User.id == user_id))


async def _notify_service_channel(
    service_channel: ServiceChannel,
    result: NotificationRunResult,
) -> None:
    global _run_event_sent
    # Skip the once-per-start heartbeat while the run is empty; an empty run must not
    # consume the slot, so the first run that actually does something still reports it.
    if _run_event_sent or result.is_empty():
        return
    _run_event_sent = True
    try:
        await service_channel.send_html(
            render_event(
                glyph="play",
                title="Subscription notification run",
                fields=[("Trigger", "first run since start")],
                extra={
                    "3d": result.three_day_sent,
                    "0d": result.zero_day_sent,
                    "post": result.post_sent,
                    "revoked": result.revoked,
                    "errors": result.errors,
                },
            )
        )
    except Exception:
        logger.exception("failed to send subscription notification service event")


def _support_url(settings: Settings) -> str:
    return f"https://t.me/{settings.bot.support_username}"
