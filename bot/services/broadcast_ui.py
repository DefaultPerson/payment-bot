"""Persistent scheduling glue for the aiogram-broadcast UI flow.

The library's UI schedules a *bound method* (``BroadcastUIManager._run_broadcast``) which
cannot be pickled into a persistent APScheduler job store, so scheduled broadcasts would be
lost on restart. Here we:

- run scheduled broadcasts through a *module-level* function (``run_scheduled_broadcast``)
  that takes only picklable args and uses the process bot, so APScheduler can serialise and
  reload the job from Redis;
- subclass the manager to enqueue that function instead of the bound method;
- inject the subclass via our own middleware (the library middleware hardcodes its class).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, TelegramObject, User
from aiogram_broadcast import BroadcastScheduler, BroadcastService
from aiogram_broadcast.ui import BroadcastUIKeyboards, BroadcastUIManager, BroadcastUITexts
from aiogram_broadcast.ui.utils import send_message_copy
from apscheduler.jobstores.redis import RedisJobStore

logger = logging.getLogger(__name__)

# Set once at startup. Scheduled-broadcast jobs may be reloaded from Redis after a restart,
# so they cannot carry a pickled bot — they use this process-wide instance instead.
_bot: Bot | None = None

_DONE_TEXT = {
    "ru": (
        "📢 Запланированная рассылка завершена: "
        "отправлено {successful}/{total}, ошибок: {failed}."
    ),
    "en": "📢 Scheduled broadcast finished: {successful}/{total} sent, {failed} failed.",
}


def set_scheduled_broadcast_bot(bot: Bot) -> None:
    """Register the process bot used by scheduled broadcast jobs."""
    global _bot
    _bot = bot


def create_redis_jobstore(redis_url: str) -> RedisJobStore:
    """Persistent APScheduler job store on Redis so scheduled broadcasts survive a restart."""
    parsed = urlparse(redis_url)
    connect_args: dict[str, Any] = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
    }
    if parsed.username:
        connect_args["username"] = parsed.username
    if parsed.password:
        connect_args["password"] = parsed.password
    db = int(parsed.path.lstrip("/") or 0)
    return RedisJobStore(
        db=db,
        jobs_key="broadcast:apscheduler.jobs",
        run_times_key="broadcast:apscheduler.run_times",
        **connect_args,
    )


async def run_scheduled_broadcast(
    subscriber_ids: list[int],
    message_data: dict[str, Any],
    chat_id: int,
    language: str = "en",
) -> None:
    """Module-level job target so a scheduled broadcast can be pickled into Redis and run
    after a restart. Copies the composed message to each subscriber, then reports to the
    admin chat that launched it."""
    bot = _bot
    if bot is None:  # pragma: no cover - defensive; bot is always set at startup
        logger.error("scheduled broadcast fired without a configured bot")
        return
    successful = 0
    failed = 0
    for user_id in subscriber_ids:
        if await send_message_copy(bot, user_id, message_data):
            successful += 1
        else:
            failed += 1
    template = _DONE_TEXT.get(language, _DONE_TEXT["en"])
    with suppress(TelegramBadRequest):
        await bot.send_message(
            chat_id,
            template.format(successful=successful, total=len(subscriber_ids), failed=failed),
        )


class PersistentBroadcastUIManager(BroadcastUIManager):
    """Broadcast UI manager whose scheduled jobs persist across restarts."""

    async def schedule_broadcast(self) -> str | None:
        if not self.scheduler or not self.scheduler.is_configured:
            return None
        state_data = await self.state.get_data()
        subscriber_ids = state_data.get("subscriber_ids", [])
        message_data = await self._data_storage.get("message_data")
        scheduled_dt = await self._data_storage.get("scheduled_datetime")
        if not subscriber_ids or not message_data or not scheduled_dt:
            return None

        from apscheduler.triggers.date import DateTrigger

        task_id = f"broadcast_{uuid.uuid4().hex[:8]}"
        assert self.scheduler.scheduler is not None
        self.scheduler.scheduler.add_job(
            func=run_scheduled_broadcast,
            trigger=DateTrigger(scheduled_dt),
            id=task_id,
            kwargs={
                "subscriber_ids": list(subscriber_ids),
                "message_data": message_data,
                "chat_id": self.user.id,
                "language": self.user.language_code or "en",
            },
            replace_existing=True,
            # If the run time fell during a restart, still fire (up to 1h late) instead of
            # silently dropping the broadcast as an APScheduler misfire.
            misfire_grace_time=3600,
        )
        return task_id


class PersistentBroadcastUIMiddleware(BaseMiddleware):
    """Inject a :class:`PersistentBroadcastUIManager` for private chats.

    Drop-in for ``aiogram_broadcast.ui.BroadcastUIMiddleware`` that builds our manager
    subclass so scheduled broadcasts are persisted instead of held in memory.
    """

    def __init__(
        self,
        service: BroadcastService,
        scheduler: BroadcastScheduler | None = None,
        manager_key: str = "broadcast_ui",
    ) -> None:
        self._service = service
        self._scheduler = scheduler
        self._manager_key = manager_key

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat: Chat | None = data.get("event_chat")
        user: User | None = data.get("event_from_user")
        manager: PersistentBroadcastUIManager | None = None

        if chat is not None and chat.type == "private" and user is not None:
            state = data.get("state")
            bot = data.get("bot")
            if state is not None and bot is not None:
                state_data = await state.get_data()
                language_code = state_data.get("ui_language_code") or user.language_code or "en"
                manager = PersistentBroadcastUIManager(
                    bot=bot,
                    user=user,
                    state=state,
                    service=self._service,
                    scheduler=self._scheduler,
                    texts=BroadcastUITexts(language_code),
                    keyboards=BroadcastUIKeyboards(language_code),
                    middleware_data=data,
                )

        data[self._manager_key] = manager
        return await handler(event, data)


class BroadcastCommandExitMiddleware(BaseMiddleware):
    """Let a bot command always escape the broadcast composer.

    While the admin is inside a ``BroadcastUIState`` the library's state-gated handlers
    delete every message in the chat — including ``/`` commands, which then never run. This
    clears the broadcast FSM state when a command arrives, so the command is handled normally
    and the admin leaves the flow (the trade-off is that a broadcast message cannot itself
    start with ``/``). Register on ``dispatcher.message.outer_middleware``.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state = data.get("state")
        text = getattr(event, "text", None)
        if state is not None and text and text.startswith("/"):
            current = await state.get_state()
            if current and current.startswith("BroadcastUIState"):
                await state.set_state(None)
        return await handler(event, data)
