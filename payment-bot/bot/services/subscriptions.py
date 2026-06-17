from __future__ import annotations

from datetime import datetime, timedelta

from bot.models import UserStatus


def calculate_extension(
    *,
    now: datetime,
    current_expires_at: datetime | None,
    days: int,
) -> datetime:
    if days <= 0:
        msg = "days must be positive"
        raise ValueError(msg)

    starts_at = current_expires_at if current_expires_at and current_expires_at > now else now
    return starts_at + timedelta(days=days)


def minimum_status_after_grant(current: UserStatus) -> UserStatus:
    if current is UserStatus.basic:
        return UserStatus.bronze
    return current
