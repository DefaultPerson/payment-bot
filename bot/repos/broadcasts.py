from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import BroadcastLog, Subscription, User

BroadcastSegment = Literal["all", "active", "inactive"]


async def get_broadcast_targets(
    session: AsyncSession,
    *,
    segment: str,
    now: datetime,
) -> list[int]:
    """Return the tg_ids for a segment, filtered SQL-side (no full ORM rows / Python set).

    ``active`` = has a subscription with ``expires_at > now``; ``inactive`` = the negation
    (no subscription, or only an expired one). The composed broadcast is copied verbatim to
    every id, so language is irrelevant here.

    Note: ``aiogram-broadcast`` materialises this list into Redis FSM state (~8 bytes/id). Fine
    to ~50k recipients; beyond that the serial, unthrottled send becomes the real limit and a
    streaming sender would be warranted.
    """
    if segment not in {"all", "active", "inactive"}:
        msg = f"unknown broadcast segment: {segment}"
        raise ValueError(msg)

    active_exists = (
        select(Subscription.id)
        .where(Subscription.user_id == User.id, Subscription.expires_at > now)
        .exists()
    )
    stmt = select(User.tg_id)
    if segment == "active":
        stmt = stmt.where(active_exists)
    elif segment == "inactive":
        stmt = stmt.where(~active_exists)
    return list(await session.scalars(stmt.order_by(User.id.asc())))


async def create_broadcast_log(
    session: AsyncSession,
    *,
    admin_user_id: int | None,
    message_text: str,
    target_filter: dict[str, object],
    started_at: datetime,
) -> BroadcastLog:
    log = BroadcastLog(
        admin_user_id=admin_user_id,
        message_text=message_text,
        target_filter=target_filter,
        total_count=0,
        success_count=0,
        failed_count=0,
        started_at=started_at,
        finished_at=None,
    )
    session.add(log)
    await session.flush()
    return log


async def finish_broadcast_log(
    session: AsyncSession,
    log: BroadcastLog,
    *,
    total_count: int,
    success_count: int,
    failed_count: int,
    finished_at: datetime,
) -> BroadcastLog:
    log.total_count = total_count
    log.success_count = success_count
    log.failed_count = failed_count
    log.finished_at = finished_at
    await session.flush()
    return log


async def finish_broadcast_log_by_id(
    session: AsyncSession,
    log_id: int,
    *,
    total_count: int,
    success_count: int,
    failed_count: int,
    finished_at: datetime,
) -> BroadcastLog:
    log = await session.get(BroadcastLog, log_id)
    if log is None:
        msg = f"broadcast log {log_id} was not found"
        raise LookupError(msg)
    return await finish_broadcast_log(
        session,
        log,
        total_count=total_count,
        success_count=success_count,
        failed_count=failed_count,
        finished_at=finished_at,
    )
