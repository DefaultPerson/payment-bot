import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.core.i18n import JsonI18n
from bot.models import Subscription, User, UserStatus
from bot.services.profile import ProfileView, build_profile_view, format_profile_text

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def make_user(*, status: UserStatus, total_active_days: int) -> User:
    return User(tg_id=123456789, status=status, total_active_days=total_active_days)


def test_profile_view_fields_match_service_contract() -> None:
    assert [field.name for field in fields(ProfileView)] == [
        "tg_id",
        "status",
        "total_active_days",
        "subscription_active",
        "expires_at",
        "days_to_next_tier",
        "referral_count",
        "referral_earned_usd",
    ]


def test_build_profile_view_reports_inactive_subscription_without_expiration() -> None:
    user = make_user(status=UserStatus.basic, total_active_days=10)
    subscription = Subscription(
        user_id=1,
        started_at=NOW - timedelta(days=20),
        expires_at=NOW - timedelta(days=1),
    )

    view = build_profile_view(user, subscription, now=NOW)

    assert view == ProfileView(
        tg_id=123456789,
        status=UserStatus.basic,
        total_active_days=10,
        subscription_active=False,
        expires_at=None,
        days_to_next_tier=80,
        referral_count=0,
        referral_earned_usd=Decimal("0.00"),
    )


def test_build_profile_view_reports_active_subscription_with_expiration() -> None:
    user = make_user(status=UserStatus.silver, total_active_days=120)
    expires_at = NOW + timedelta(days=10)
    subscription = Subscription(
        user_id=1,
        started_at=NOW - timedelta(days=120),
        expires_at=expires_at,
    )

    view = build_profile_view(user, subscription, now=NOW)

    assert view.subscription_active is True
    assert view.expires_at == expires_at
    assert view.days_to_next_tier == 60


@pytest.mark.parametrize(
    ("status", "total_active_days", "expected"),
    [
        (UserStatus.basic, 0, 90),
        (UserStatus.basic, 89, 1),
        (UserStatus.basic, 90, None),
        (UserStatus.bronze, 30, 60),
        (UserStatus.bronze, 90, None),
        (UserStatus.silver, 90, 90),
        (UserStatus.silver, 179, 1),
        (UserStatus.silver, 180, None),
        (UserStatus.gold, 0, None),
        (UserStatus.gold, 250, None),
    ],
)
def test_build_profile_view_calculates_days_to_next_tier(
    status: UserStatus,
    total_active_days: int,
    expected: int | None,
) -> None:
    user = make_user(status=status, total_active_days=total_active_days)

    view = build_profile_view(user, None, now=NOW)

    assert view.days_to_next_tier == expected


def test_format_profile_text_uses_i18n_keys(tmp_path) -> None:
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "en.json").write_text(
        json.dumps(
            {
                "profile.text": (
                    "tg={tg_id}; status={status}; days={total_active_days}; "
                    "subscription={subscription_status}; expires={expires_at}; "
                    "next={days_to_next_tier}; referrals={referral_count}; "
                    "earned={referral_earned_usd}"
                ),
                "profile.status.bronze": "Bronze",
                "profile.subscription.active": "Active",
                "profile.subscription.inactive": "Inactive",
                "profile.expires_at.none": "No active subscription",
                "profile.next_tier.none": "Highest tier",
            },
        ),
        encoding="utf-8",
    )
    i18n = JsonI18n(locales_dir=locales_dir, fallback_language="en")
    expires_at = NOW + timedelta(days=10)
    view = ProfileView(
        tg_id=123456789,
        status=UserStatus.bronze,
        total_active_days=40,
        subscription_active=True,
        expires_at=expires_at,
        days_to_next_tier=50,
        referral_count=0,
        referral_earned_usd=Decimal("0.00"),
    )

    text = format_profile_text(view, i18n, "en")

    assert text == (
        "tg=123456789; status=Bronze; days=40; subscription=Active; "
        f"expires={expires_at.isoformat()}; next=50; referrals=0; earned=0.00"
    )
