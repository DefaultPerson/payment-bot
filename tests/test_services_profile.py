import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.core.i18n import JsonI18n
from bot.models import Subscription, User, UserStatus
from bot.repos.referrals import ReferralStats
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
        "referral_unpaid_earned_usd",
        "invite_link",
        "min_withdraw_usd",
        "plan_label",
    ]


def test_build_profile_view_reports_inactive_subscription_without_expiration() -> None:
    user = make_user(status=UserStatus.basic, total_active_days=10)
    subscription = Subscription(
        user_id=1,
        started_at=NOW - timedelta(days=20),
        expires_at=NOW - timedelta(days=1),
    )

    view = build_profile_view(
        user, subscription, now=NOW, bot_username="HMPTbot", min_withdraw_usd=Decimal("10")
    )

    assert view == ProfileView(
        tg_id=123456789,
        status=UserStatus.basic,
        total_active_days=10,
        subscription_active=False,
        expires_at=None,
        days_to_next_tier=20,
        referral_count=0,
        referral_earned_usd=Decimal("0.00"),
        referral_unpaid_earned_usd=Decimal("0.00"),
        invite_link="https://t.me/HMPTbot?start=ref_123456789",
        min_withdraw_usd=Decimal("10"),
        plan_label="",
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
    # Lived coverage: 120 purchased days minus 10 still remaining = 110 consumed, so 40 more
    # to the gold threshold (150). The 10 days still ahead do not count as tenure yet.
    assert view.total_active_days == 110
    assert view.days_to_next_tier == 40


@pytest.mark.parametrize(
    ("status", "total_active_days", "expected"),
    [
        # Tiers (default): bronze 30d, silver 90d, gold 150d. Next tier above the current status.
        (UserStatus.basic, 0, 30),
        (UserStatus.basic, 29, 1),
        (UserStatus.bronze, 30, 60),
        (UserStatus.bronze, 89, 1),
        (UserStatus.silver, 90, 60),
        (UserStatus.silver, 149, 1),
        (UserStatus.gold, 150, None),
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
                    "earned={referral_earned_usd}; unpaid={referral_unpaid_earned_usd}"
                ),
                "profile.status.bronze": "Bronze",
                "profile.subscription.active": "Active",
                "profile.subscription.inactive": "Inactive",
                "profile.expires_at.none": "No active subscription",
                "profile.next_tier.none": "Highest tier",
                "profile.next_tier.days": "{days} days",
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
        referral_unpaid_earned_usd=Decimal("0.00"),
        invite_link="https://t.me/HMPTbot?start=ref_123456789",
        min_withdraw_usd=Decimal("10"),
        plan_label="Main",
    )

    text = format_profile_text(view, i18n, "en")

    assert text == (
        "tg=123456789; status=Bronze; days=40; subscription=Active; "
        "expires=2026-06-27 12:00 UTC; next=50 days; referrals=0; earned=0.00; unpaid=0.00"
    )


def test_build_profile_view_uses_referral_stats() -> None:
    user = make_user(status=UserStatus.basic, total_active_days=10)
    stats = ReferralStats(
        referral_count=3,
        earned_usd=Decimal("21.50"),
        unpaid_earned_usd=Decimal("11.50"),
    )

    view = build_profile_view(user, None, now=NOW, referral_stats=stats)

    assert view.referral_count == 3
    assert view.referral_earned_usd == Decimal("21.50")
    assert view.referral_unpaid_earned_usd == Decimal("11.50")
