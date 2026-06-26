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
from bot.core.service_events import render_event, user_mention
from bot.models import Subscription
from bot.repos.subscriptions import get_subscription_for_user
from bot.repos.users import get_user_by_tg_id
from bot.services.access import allowed_chat_ids_for_plan

logger = logging.getLogger(__name__)


class SupportsJoinRequestBot(Protocol):
    async def approve_chat_join_request(self, *, chat_id: int, user_id: int) -> object: ...

    async def decline_chat_join_request(self, *, chat_id: int, user_id: int) -> object: ...


async def active_subscription_for(
    session: AsyncSession,
    *,
    tg_id: int,
    now: datetime,
) -> Subscription | None:
    """The joining ``tg_id``'s subscription if it is currently active, else ``None``.

    Authorization is bound to the joining account, not to a forwardable invite link —
    that binding is exactly what closes the link-sharing gap of the old member-limited
    one-time links.
    """
    user = await get_user_by_tg_id(session, tg_id)
    if user is None or user.id is None:
        return None
    subscription = await get_subscription_for_user(session, user.id)
    if subscription is not None and subscription.expires_at > now:
        return subscription
    return None


async def has_active_subscription(
    session: AsyncSession,
    *,
    tg_id: int,
    now: datetime,
) -> bool:
    return await active_subscription_for(session, tg_id=tg_id, now=now) is not None


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
            subscription = await active_subscription_for(session, tg_id=tg_id, now=now)
            plan_code = subscription.plan_code if subscription is not None else None
    except SQLAlchemyError:
        # Leave the request pending rather than risk declining a paying user; the bot
        # re-evaluates it on the next delivery and the expiry watcher is unaffected.
        logger.exception(
            "failed to evaluate join request; leaving it pending",
            extra={"chat_id": chat_id, "tg_id": tg_id},
        )
        return

    # Approve only when the subscription is active AND its plan unlocks this chat.
    approved = subscription is not None and chat_id in allowed_chat_ids_for_plan(
        settings, plan_code
    )

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
        await _notify_declined(
            service_channel,
            chat_id=chat_id,
            tg_id=tg_id,
            username=event.from_user.username,
        )


async def _notify_declined(
    service_channel: ServiceChannel,
    *,
    chat_id: int,
    tg_id: int,
    username: str | None = None,
) -> None:
    try:
        await service_channel.send_html(
            render_event(
                glyph="cross",
                title="Join request declined",
                fields=[
                    ("User", user_mention(tg_id, username)),
                    ("Reason", "no active subscription"),
                ],
                extra={"chat_id": chat_id},
            )
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
