from datetime import UTC, datetime, timedelta

import pytest

from bot.models import UserStatus
from bot.services.subscriptions import (
    calculate_extension,
    minimum_status_after_grant,
    status_after_total_active_days,
)


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


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (UserStatus.basic, UserStatus.bronze),
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
        (UserStatus.basic, 1, UserStatus.bronze),
        (UserStatus.basic, 90, UserStatus.silver),
        (UserStatus.bronze, 180, UserStatus.gold),
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
    assert (
        status_after_total_active_days(
            UserStatus.basic,
            60,
            silver_active_days=60,
            gold_active_days=120,
        )
        is UserStatus.silver
    )
    assert (
        status_after_total_active_days(
            UserStatus.bronze,
            120,
            silver_active_days=60,
            gold_active_days=120,
        )
        is UserStatus.gold
    )
