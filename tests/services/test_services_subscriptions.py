from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.models import UserStatus
from bot.services.subscriptions import (
    calculate_extension,
    calculate_plan_change_expiry,
    minimum_status_after_grant,
    status_after_total_active_days,
)

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def test_calculate_extension_starts_from_now_without_current_subscription() -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    new_expires_at = calculate_extension(now=now, current_expires_at=None, days=10)

    assert new_expires_at == now + timedelta(days=10)


def test_calculate_extension_extends_from_future_expiration() -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    future = now + timedelta(days=5)

    assert calculate_extension(now=now, current_expires_at=future, days=10) == future + timedelta(
        days=10,
    )


def test_calculate_extension_extends_from_now_for_expired_subscription() -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    past = now - timedelta(days=5)

    assert calculate_extension(now=now, current_expires_at=past, days=10) == now + timedelta(
        days=10,
    )


@pytest.mark.parametrize("days", [0, -1])
def test_calculate_extension_rejects_non_positive_days(days: int) -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="days must be positive"):
        calculate_extension(now=now, current_expires_at=None, days=days)


def test_plan_change_upgrade_prorates_carried_days_by_value() -> None:
    # 300 remaining days, old $1/day, new $2/day -> carry 300*(1/2)=150, + 30 purchased.
    expiry = calculate_plan_change_expiry(
        now=NOW,
        current_expires_at=NOW + timedelta(days=300),
        purchased_days=30,
        is_upgrade=True,
        old_rate=Decimal("1"),
        new_rate=Decimal("2"),
    )
    assert expiry == NOW + timedelta(days=150 + 30)


def test_plan_change_upgrade_never_grows_carry_when_new_is_cheaper() -> None:
    # Upgrade flagged (e.g. by access breadth) but the new plan is cheaper: clamp keeps the
    # full remaining time, never grows it.
    expiry = calculate_plan_change_expiry(
        now=NOW,
        current_expires_at=NOW + timedelta(days=100),
        purchased_days=30,
        is_upgrade=True,
        old_rate=Decimal("2"),
        new_rate=Decimal("1"),
    )
    assert expiry == NOW + timedelta(days=100 + 30)


def test_plan_change_non_upgrade_carries_full_remaining() -> None:
    expiry = calculate_plan_change_expiry(
        now=NOW,
        current_expires_at=NOW + timedelta(days=100),
        purchased_days=30,
        is_upgrade=False,
        old_rate=Decimal("1"),
        new_rate=Decimal("2"),
    )
    assert expiry == NOW + timedelta(days=100 + 30)


def test_plan_change_expired_subscription_has_no_carry() -> None:
    expiry = calculate_plan_change_expiry(
        now=NOW,
        current_expires_at=NOW - timedelta(days=5),
        purchased_days=30,
        is_upgrade=True,
        old_rate=Decimal("1"),
        new_rate=Decimal("2"),
    )
    assert expiry == NOW + timedelta(days=30)


def test_plan_change_unknown_rates_carry_full_remaining() -> None:
    expiry = calculate_plan_change_expiry(
        now=NOW,
        current_expires_at=NOW + timedelta(days=100),
        purchased_days=30,
        is_upgrade=True,
        old_rate=None,
        new_rate=None,
    )
    assert expiry == NOW + timedelta(days=100 + 30)


def test_plan_change_rejects_non_positive_purchased_days() -> None:
    with pytest.raises(ValueError, match="purchased_days must be positive"):
        calculate_plan_change_expiry(
            now=NOW,
            current_expires_at=None,
            purchased_days=0,
            is_upgrade=False,
            old_rate=None,
            new_rate=None,
        )


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        # A fresh grant (1 active day, month 1) earns no tier — bronze starts at month 2.
        (UserStatus.basic, UserStatus.basic),
        (UserStatus.bronze, UserStatus.bronze),
        (UserStatus.silver, UserStatus.silver),
        (UserStatus.gold, UserStatus.gold),
    ],
)
def test_minimum_status_after_grant_never_downgrades_existing_tiers(
    current: UserStatus,
    expected: UserStatus,
) -> None:
    assert minimum_status_after_grant(current) is expected


@pytest.mark.parametrize(
    ("current", "total_active_days", "expected"),
    [
        (UserStatus.basic, 0, UserStatus.basic),
        (UserStatus.basic, 29, UserStatus.basic),
        (UserStatus.basic, 30, UserStatus.bronze),
        (UserStatus.basic, 90, UserStatus.silver),
        (UserStatus.bronze, 150, UserStatus.gold),
        (UserStatus.gold, 10, UserStatus.gold),
    ],
)
def test_status_after_total_active_days_promotes_without_downgrading(
    current: UserStatus,
    total_active_days: int,
    expected: UserStatus,
) -> None:
    assert status_after_total_active_days(current, total_active_days) is expected


def test_status_after_total_active_days_uses_configurable_thresholds() -> None:
    # silver from month 3 (>=60 days), gold from month 5 (>=120 days).
    assert (
        status_after_total_active_days(
            UserStatus.basic,
            60,
            bronze_active_months=2,
            silver_active_months=3,
            gold_active_months=5,
        )
        is UserStatus.silver
    )
    assert (
        status_after_total_active_days(
            UserStatus.bronze,
            120,
            bronze_active_months=2,
            silver_active_months=3,
            gold_active_months=5,
        )
        is UserStatus.gold
    )
