from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bot.core.i18n import JsonI18n
from bot.models import Subscription, User, UserStatus
from bot.repos.referrals import ReferralStats
from bot.services.subscriptions import (
    BRONZE_ACTIVE_MONTHS,
    GOLD_ACTIVE_MONTHS,
    SILVER_ACTIVE_MONTHS,
    STATUS_RANK,
    SupportsLoyaltyThresholds,
    tier_threshold_days,
)


@dataclass(frozen=True, slots=True)
class ProfileView:
    tg_id: int
    status: UserStatus
    total_active_days: int
    subscription_active: bool
    expires_at: datetime | None
    days_to_next_tier: int | None
    referral_count: int
    referral_earned_usd: Decimal
    referral_unpaid_earned_usd: Decimal


def build_profile_view(
    user: User,
    subscription: Subscription | None,
    *,
    now: datetime,
    referral_stats: ReferralStats | None = None,
    loyalty: SupportsLoyaltyThresholds | None = None,
) -> ProfileView:
    total_active_days = user.total_active_days or 0
    status = user.status or UserStatus.basic
    subscription_active = subscription is not None and subscription.expires_at > now

    return ProfileView(
        tg_id=user.tg_id,
        status=status,
        total_active_days=total_active_days,
        subscription_active=subscription_active,
        expires_at=subscription.expires_at if subscription_active and subscription else None,
        days_to_next_tier=_days_to_next_tier(status, total_active_days, loyalty),
        referral_count=referral_stats.referral_count if referral_stats is not None else 0,
        referral_earned_usd=(
            referral_stats.earned_usd if referral_stats is not None else Decimal("0.00")
        ),
        referral_unpaid_earned_usd=(
            referral_stats.unpaid_earned_usd if referral_stats is not None else Decimal("0.00")
        ),
    )


def format_profile_text(view: ProfileView, i18n: JsonI18n, lang_code: str) -> str:
    subscription_key = (
        "profile.subscription.active"
        if view.subscription_active
        else "profile.subscription.inactive"
    )
    expires_at = (
        view.expires_at.isoformat()
        if view.expires_at is not None
        else i18n.gettext("profile.expires_at.none", lang_code)
    )
    days_to_next_tier: int | str = (
        view.days_to_next_tier
        if view.days_to_next_tier is not None
        else i18n.gettext("profile.next_tier.none", lang_code)
    )

    return i18n.gettext(
        "profile.text",
        lang_code,
        tg_id=view.tg_id,
        status=i18n.gettext(f"profile.status.{view.status.value}", lang_code),
        status_value=view.status.value,
        total_active_days=view.total_active_days,
        subscription_active=view.subscription_active,
        subscription_status=i18n.gettext(subscription_key, lang_code),
        expires_at=expires_at,
        days_to_next_tier=days_to_next_tier,
        referral_count=view.referral_count,
        referral_earned_usd=f"{view.referral_earned_usd:.2f}",
        referral_unpaid_earned_usd=f"{view.referral_unpaid_earned_usd:.2f}",
    )


def _days_to_next_tier(
    status: UserStatus,
    total_active_days: int,
    loyalty: SupportsLoyaltyThresholds | None = None,
) -> int | None:
    """Active days remaining until the next loyalty tier, or None at the top tier."""
    if loyalty is not None:
        tiers = (
            (UserStatus.bronze, loyalty.bronze_active_months),
            (UserStatus.silver, loyalty.silver_active_months),
            (UserStatus.gold, loyalty.gold_active_months),
        )
    else:
        tiers = (
            (UserStatus.bronze, BRONZE_ACTIVE_MONTHS),
            (UserStatus.silver, SILVER_ACTIVE_MONTHS),
            (UserStatus.gold, GOLD_ACTIVE_MONTHS),
        )
    for tier, active_months in tiers:
        if STATUS_RANK[status] < STATUS_RANK[tier]:
            return max(0, tier_threshold_days(active_months) - total_active_days)
    return None
