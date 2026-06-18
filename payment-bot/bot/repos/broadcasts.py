from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import BroadcastLog, Subscription, User

BroadcastSegment = Literal["all", "active", "inactive"]


@dataclass(frozen=True, slots=True)
class BroadcastTarget:
    tg_id: int
    lang_code: str


async def get_broadcast_targets(
    session: AsyncSession,
    *,
    segment: str,
    now: datetime,
) -> list[BroadcastTarget]:
    if segment not in {"all", "active", "inactive"}:
        msg = f"unknown broadcast segment: {segment}"
        raise ValueError(msg)

    users_result = await session.scalars(select(User).order_by(User.id.asc()))
    users = list(users_result.all())
    if segment == "all":
        return [_target(user) for user in users]

    active_result = await session.scalars(
        select(Subscription).where(Subscription.expires_at > now),
    )
    active_user_ids = {subscription.user_id for subscription in active_result.all()}
    if segment == "active":
        return [_target(user) for user in users if user.id in active_user_ids]
    return [_target(user) for user in users if user.id not in active_user_ids]


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


def _target(user: User) -> BroadcastTarget:
    return BroadcastTarget(tg_id=user.tg_id, lang_code=user.lang_code)
