"""Stamp ``users.last_active_at`` on every interaction so DAU/MAU can be queried.

Registered on ``dispatcher.update.middleware`` (inner) so ``event_from_user`` is already
populated by aiogram's context middleware. Writes are throttled through Redis (one Postgres
UPDATE per user per window) and fully best-effort: any Redis/DB error is swallowed so analytics
never blocks message handling.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.core.db import session_scope
from bot.repos.users import touch_user_last_active

logger = logging.getLogger(__name__)


class ActivityMiddleware(BaseMiddleware):
    def __init__(
        self,
        session_factory: async_sessionmaker,
        redis_client: Any,
        *,
        throttle_seconds: int = 600,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._throttle_seconds = throttle_seconds

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is not None:
            await self._maybe_bump(user.id)
        return await handler(event, data)

    async def _maybe_bump(self, tg_id: int) -> None:
        # The Redis NX key coalesces a burst of updates into a single DB write per window.
        try:
            acquired = await self._redis.set(
                f"active:bump:{tg_id}",
                1,
                nx=True,
                ex=self._throttle_seconds,
            )
            if not acquired:
                return
            async with session_scope(self._session_factory) as session:
                await touch_user_last_active(session, tg_id=tg_id, now=datetime.now(UTC))
        except Exception:  # noqa: BLE001 - analytics must never break handling
            with suppress(Exception):
                logger.debug("activity bump failed", extra={"tg_id": tg_id}, exc_info=True)
