from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from aiogram import Router
from aiogram.types import ChatJoinRequest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.logger import ServiceChannel
from bot.repos.subscriptions import get_subscription_for_user
from bot.repos.users import get_user_by_tg_id

logger = logging.getLogger(__name__)


class SupportsJoinRequestBot(Protocol):
    async def approve_chat_join_request(self, *, chat_id: int, user_id: int) -> object: ...

    async def decline_chat_join_request(self, *, chat_id: int, user_id: int) -> object: ...


async def has_active_subscription(
    session: AsyncSession,
    *,
    tg_id: int,
    now: datetime,
) -> bool:
    """Whether the *joining* ``tg_id`` may currently enter protected chats.

    Authorization is bound to the joining account, not to a forwardable invite link —
    that binding is exactly what closes the link-sharing gap of the old member-limited
    one-time links.
    """
    user = await get_user_by_tg_id(session, tg_id)
    if user is None or user.id is None:
        return False
    subscription = await get_subscription_for_user(session, user.id)
    return subscription is not None and subscription.expires_at > now


async def handle_chat_join_request(
    event: ChatJoinRequest,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bot: SupportsJoinRequestBot,
    service_channel: ServiceChannel,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    chat_id = event.chat.id
    tg_id = event.from_user.id

    protected_chat_ids = {chat.chat_id for chat in settings.access.protected_chats}
    if chat_id not in protected_chat_ids:
        logger.warning(
            "ignoring join request for unmanaged chat",
            extra={"chat_id": chat_id, "tg_id": tg_id},
        )
        return

    try:
        async with session_scope(session_factory) as session:
            approved = await has_active_subscription(session, tg_id=tg_id, now=now)
    except SQLAlchemyError:
        # Leave the request pending rather than risk declining a paying user; the bot
        # re-evaluates it on the next delivery and the expiry watcher is unaffected.
        logger.exception(
            "failed to evaluate join request; leaving it pending",
            extra={"chat_id": chat_id, "tg_id": tg_id},
        )
        return

    try:
        if approved:
            await bot.approve_chat_join_request(chat_id=chat_id, user_id=tg_id)
        else:
            await bot.decline_chat_join_request(chat_id=chat_id, user_id=tg_id)
    except Exception:
        logger.exception(
            "failed to answer chat join request",
            extra={"chat_id": chat_id, "tg_id": tg_id, "approved": approved},
        )
        return

    logger.info(
        "chat join request handled",
        extra={"chat_id": chat_id, "tg_id": tg_id, "approved": approved},
    )
    if not approved:
        await _notify_declined(service_channel, chat_id=chat_id, tg_id=tg_id)


async def _notify_declined(
    service_channel: ServiceChannel,
    *,
    chat_id: int,
    tg_id: int,
) -> None:
    try:
        await service_channel.send_html(
            "<b>join request declined</b>\n"
            "reason=no active subscription\n"
            f"chat_id={chat_id}\n"
            f"tg_id={tg_id}"
        )
    except Exception:
        logger.exception(
            "failed to send join request decline event",
            extra={"chat_id": chat_id, "tg_id": tg_id},
        )


def create_join_request_router() -> Router:
    router = Router(name="join_request")
    router.chat_join_request()(handle_chat_join_request)
    return router
