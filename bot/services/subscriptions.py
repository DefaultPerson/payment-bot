from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from bot.models import UserStatus

DAYS_PER_MONTH = 30
# Default loyalty thresholds as months of tenure (1-indexed: the month the tier starts
# from). Month 1 = the first 30 active days = basic (no loyalty discount).
BRONZE_ACTIVE_MONTHS = 2
SILVER_ACTIVE_MONTHS = 4
GOLD_ACTIVE_MONTHS = 6
STATUS_RANK = {
    UserStatus.basic: 0,
    UserStatus.bronze: 1,
    UserStatus.silver: 2,
    UserStatus.gold: 3,
}


class SupportsLoyaltyThresholds(Protocol):
    bronze_active_months: int
    silver_active_months: int
    gold_active_months: int


def tier_threshold_days(active_months: int) -> int:
    """Active days required to reach a tier that starts from month ``active_months``.

    The Nth month of tenure begins after (N-1) completed 30-day months.
    """
    return max(0, (active_months - 1) * DAYS_PER_MONTH)


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
    bronze_active_months: int = BRONZE_ACTIVE_MONTHS,
    silver_active_months: int = SILVER_ACTIVE_MONTHS,
    gold_active_months: int = GOLD_ACTIVE_MONTHS,
) -> UserStatus:
    earned = UserStatus.basic
    if total_active_days >= tier_threshold_days(gold_active_months):
        earned = UserStatus.gold
    elif total_active_days >= tier_threshold_days(silver_active_months):
        earned = UserStatus.silver
    elif total_active_days >= tier_threshold_days(bronze_active_months):
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
        bronze_active_months=loyalty.bronze_active_months,
        silver_active_months=loyalty.silver_active_months,
        gold_active_months=loyalty.gold_active_months,
    )
