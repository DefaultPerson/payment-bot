from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from bot.models import UserStatus

SILVER_ACTIVE_DAYS = 90
GOLD_ACTIVE_DAYS = 180
STATUS_RANK = {
    UserStatus.basic: 0,
    UserStatus.bronze: 1,
    UserStatus.silver: 2,
    UserStatus.gold: 3,
}


class SupportsLoyaltyThresholds(Protocol):
    silver_active_days: int
    gold_active_days: int


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
    *,
    silver_active_days: int = SILVER_ACTIVE_DAYS,
    gold_active_days: int = GOLD_ACTIVE_DAYS,
) -> UserStatus:
    earned = UserStatus.basic
    if total_active_days >= gold_active_days:
        earned = UserStatus.gold
    elif total_active_days >= silver_active_days:
        earned = UserStatus.silver
    elif total_active_days > 0:
        earned = UserStatus.bronze

    return max(current, earned, key=STATUS_RANK.__getitem__)


def status_after_total_active_days_for_policy(
    current: UserStatus,
    total_active_days: int,
    loyalty: SupportsLoyaltyThresholds | None,
) -> UserStatus:
    if loyalty is None:
        return status_after_total_active_days(current, total_active_days)
    return status_after_total_active_days(
        current,
        total_active_days,
        silver_active_days=loyalty.silver_active_days,
        gold_active_days=loyalty.gold_active_days,
    )
