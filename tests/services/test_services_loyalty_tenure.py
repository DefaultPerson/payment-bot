"""Loyalty tenure from LIVED coverage (consumed), not purchased days.

Guards the fix for status being instantly buyable: a tier must be earned by real elapsed
covered time, so bulk/ladder purchases confer nothing up front.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.models import UserStatus
from bot.services.subscriptions import consumed_coverage_days, effective_status

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


# --- consumed_coverage_days -------------------------------------------------------------


def test_fresh_purchase_consumes_nothing() -> None:
    # Buying 6m (180d) also pushes expiry 180d out, so nothing is lived yet.
    assert consumed_coverage_days(180, NOW + timedelta(days=180), NOW) == 0


def test_consumed_grows_as_covered_time_elapses() -> None:
    # 180 purchased, only 30 remaining => 150 days have been lived under coverage.
    assert consumed_coverage_days(180, NOW + timedelta(days=30), NOW) == 150


def test_expired_subscription_counts_all_purchased_days_as_lived() -> None:
    assert consumed_coverage_days(90, NOW - timedelta(days=1), NOW) == 90


def test_no_subscription_counts_all_purchased_days_as_lived() -> None:
    assert consumed_coverage_days(90, None, NOW) == 90


def test_bonus_days_beyond_purchased_are_clamped_to_zero() -> None:
    # promo extra_days extend expiry without crediting total_active_days -> no negative tenure.
    assert consumed_coverage_days(30, NOW + timedelta(days=40), NOW) == 0


def test_naive_expiry_is_treated_as_utc() -> None:
    # SQLite round-trips timestamptz as naive; must not raise and must compute correctly.
    naive_future = (NOW + timedelta(days=30)).replace(tzinfo=None)
    assert consumed_coverage_days(180, naive_future, NOW) == 150


# --- effective_status: the three closed exploits ----------------------------------------


def test_new_six_month_buyer_is_not_instantly_gold() -> None:
    # A single 6m buy must not grant Gold before any time is lived.
    status = effective_status(UserStatus.basic, 180, NOW + timedelta(days=180), NOW, None)
    assert status is UserStatus.basic


def test_six_month_buyer_reaches_gold_after_lived_coverage() -> None:
    # ~5 lived months (150 consumed) reaches the gold threshold.
    status = effective_status(UserStatus.basic, 180, NOW + timedelta(days=30), NOW, None)
    assert status is UserStatus.gold


def test_ladder_one_then_three_months_stays_basic() -> None:
    # After buying 1m (30 purchased, 30 remaining) consumed = 0, so the very next 3m buy is
    # still quoted at basic instead of a bronze discount.
    status = effective_status(UserStatus.basic, 30, NOW + timedelta(days=30), NOW, None)
    assert status is UserStatus.basic


def test_wait_then_bulk_only_credits_actually_lived_coverage() -> None:
    # Bought 1m long ago (fully lived), idled, now buys 6m: 210 purchased, 180 remaining ->
    # consumed = 30, i.e. only the one month actually lived. NOT gold.
    status = effective_status(UserStatus.basic, 210, NOW + timedelta(days=180), NOW, None)
    assert status is UserStatus.bronze


# --- effective_status: ratchet + None current -------------------------------------------


def test_status_never_downgrades_below_stored_high_water() -> None:
    # A gold user whose current consumed is 0 keeps gold (never demoted on read/renewal).
    status = effective_status(UserStatus.gold, 30, NOW + timedelta(days=30), NOW, None)
    assert status is UserStatus.gold


def test_none_current_status_defaults_to_basic() -> None:
    assert effective_status(None, 0, None, NOW, None) is UserStatus.basic


# --- status grace / decay after expiry (loyalty=None => default 7-day grace) ---------------


def test_status_kept_while_subscription_active() -> None:
    # Gold tenure, still active: current status floors the tier -> gold.
    assert effective_status(UserStatus.gold, 150, NOW + timedelta(days=5), NOW, None) is (
        UserStatus.gold
    )


def test_status_kept_within_grace_window_after_expiry() -> None:
    # Expired 3 days ago (< 7-day grace): a short lapse keeps the earned tier.
    assert effective_status(UserStatus.gold, 150, NOW - timedelta(days=3), NOW, None) is (
        UserStatus.gold
    )


def test_status_forfeited_after_grace_window() -> None:
    # Expired 10 days ago (> 7-day grace): the tier is forfeited back to basic.
    assert effective_status(UserStatus.gold, 150, NOW - timedelta(days=10), NOW, None) is (
        UserStatus.basic
    )


def test_status_forfeit_ignores_never_subscribed() -> None:
    # expires_at is None (never subscribed) is not a forfeit — no crash, stays basic.
    assert effective_status(UserStatus.basic, 0, None, NOW, None) is UserStatus.basic
