from __future__ import annotations

from datetime import datetime, timedelta

from bot.models import UserStatus

SILVER_ACTIVE_DAYS = 90
GOLD_ACTIVE_DAYS = 180
STATUS_RANK = {
    UserStatus.basic: 0,
    UserStatus.bronze: 1,
    UserStatus.silver: 2,
    UserStatus.gold: 3,
}


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
    return status_after_total_active_days(current, 1)


def status_after_total_active_days(
    current: UserStatus,
    total_active_days: int,
) -> UserStatus:
    earned = UserStatus.basic
    if total_active_days >= GOLD_ACTIVE_DAYS:
        earned = UserStatus.gold
    elif total_active_days >= SILVER_ACTIVE_DAYS:
        earned = UserStatus.silver
    elif total_active_days > 0:
        earned = UserStatus.bronze

    return max(current, earned, key=STATUS_RANK.__getitem__)
