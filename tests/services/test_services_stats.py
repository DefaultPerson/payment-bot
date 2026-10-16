from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.services.stats import StatsPeriod, resolve_period_bounds

NOW = datetime(2026, 6, 26, 15, 30, 45, tzinfo=UTC)


def test_today_bounds_start_of_day() -> None:
    start, end = resolve_period_bounds(StatsPeriod.today, NOW)
    assert start == datetime(2026, 6, 26, 0, 0, 0, tzinfo=UTC)
    assert end == NOW


def test_rolling_windows() -> None:
    assert resolve_period_bounds(StatsPeriod.d7, NOW) == (NOW - timedelta(days=7), NOW)
    assert resolve_period_bounds(StatsPeriod.d30, NOW) == (NOW - timedelta(days=30), NOW)


def test_all_has_no_lower_bound() -> None:
    start, end = resolve_period_bounds(StatsPeriod.all, NOW)
    assert start is None
    assert end == NOW


def test_period_enum_round_trips_from_callback_suffix() -> None:
    assert StatsPeriod("today") is StatsPeriod.today
    assert StatsPeriod("7d") is StatsPeriod.d7
    assert StatsPeriod("30d") is StatsPeriod.d30
    assert StatsPeriod("all") is StatsPeriod.all
