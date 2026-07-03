from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.core.service_events import fmt_dt
from bot.core.text_emoji import text_emoji
from bot.models import Subscription, User, UserStatus
from bot.repos.referrals import ReferralStats
from bot.repos.tariffs import default_plan, get_plan
from bot.services.subscriptions import (
    BRONZE_ACTIVE_MONTHS,
    GOLD_ACTIVE_MONTHS,
    SILVER_ACTIVE_MONTHS,
    STATUS_RANK,
    SupportsLoyaltyThresholds,
    consumed_coverage_days,
    effective_status,
    subscription_active_at,
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
    invite_link: str
    min_withdraw_usd: Decimal
    plan_label: str


def build_profile_view(
    user: User,
    subscription: Subscription | None,
    *,
    now: datetime,
    referral_stats: ReferralStats | None = None,
    loyalty: SupportsLoyaltyThresholds | None = None,
    bot_username: str = "",
    min_withdraw_usd: Decimal = Decimal("0"),
    plan_label: str = "",
) -> ProfileView:
    expires_at = subscription.expires_at if subscription is not None else None
    subscription_active = expires_at is not None and expires_at > now
    # Loyalty is driven by LIVED coverage, so the status, the shown day count and the
    # next-tier countdown are all the same quantity (consumed), never the purchased total.
    consumed = consumed_coverage_days(user.total_active_days, expires_at, now)
    status = effective_status(
        user.status, user.total_active_days, expires_at, now, loyalty
    )

    return ProfileView(
        tg_id=user.tg_id,
        status=status,
        total_active_days=consumed,
        subscription_active=subscription_active,
        expires_at=expires_at if subscription_active else None,
        days_to_next_tier=_days_to_next_tier(status, consumed, loyalty),
        referral_count=referral_stats.referral_count if referral_stats is not None else 0,
        referral_earned_usd=(
            referral_stats.earned_usd if referral_stats is not None else Decimal("0.00")
        ),
        referral_unpaid_earned_usd=(
            referral_stats.available_usd if referral_stats is not None else Decimal("0.00")
        ),
        invite_link=f"https://t.me/{bot_username}?start=ref_{user.tg_id}",
        min_withdraw_usd=min_withdraw_usd,
        plan_label=plan_label,
    )


def welcome_status_label(
    i18n: JsonI18n,
    lang_code: str,
    user: User,
    subscription: Subscription | None,
    now: datetime,
    loyalty: SupportsLoyaltyThresholds | None = None,
) -> str:
    """Status shown on the /start welcome screen.

    ``Free`` when there is no active subscription; otherwise the localized loyalty tier
    (Basic/Bronze/Silver/Gold). The real tier is always visible in the profile view.
    """
    expires_at = subscription.expires_at if subscription is not None else None
    if not subscription_active_at(expires_at, now):
        return i18n.gettext("start.status.free", lang_code)
    status = effective_status(user.status, user.total_active_days, expires_at, now, loyalty)
    return i18n.gettext(f"profile.status.{status.value}", lang_code)


def profile_plan_label(
    settings: Settings,
    i18n: JsonI18n,
    lang_code: str,
    subscription: Subscription | None,
    now: datetime,
) -> str:
    """Plan shown in the profile: the plan title when the subscription is active, else "Free".

    Fail-safe: an unknown/retired plan_code falls back to the default plan (mirrors
    ``bot.services.access._resolve_plan_or_default``); a plan with no title shows its code.
    """
    expires_at = subscription.expires_at if subscription is not None else None
    if subscription is None or not subscription_active_at(expires_at, now):
        return i18n.gettext("start.status.free", lang_code)
    plan = None
    if subscription.plan_code is not None:
        try:
            plan = get_plan(settings, subscription.plan_code)
        except LookupError:
            plan = None
    if plan is None:
        try:
            plan = default_plan(settings)
        except LookupError:
            plan = None
    if plan is None:
        return i18n.gettext("start.status.free", lang_code)
    return plan.title or plan.code


def format_profile_text(view: ProfileView, i18n: JsonI18n, lang_code: str) -> str:
    subscription_key = (
        "profile.subscription.active"
        if view.subscription_active
        else "profile.subscription.inactive"
    )
    expires_at = (
        fmt_dt(view.expires_at)
        if view.expires_at is not None
        else i18n.gettext("profile.expires_at.none", lang_code)
    )
    days_to_next_tier: str = (
        i18n.gettext("profile.next_tier.days", lang_code, days=view.days_to_next_tier)
        if view.days_to_next_tier is not None
        else i18n.gettext("profile.next_tier.none", lang_code)
    )

    return i18n.gettext(
        "profile.text",
        lang_code,
        profile_emoji=text_emoji("profile"),
        referral_emoji=text_emoji("people"),
        free_emoji=text_emoji("free"),
        tg_id=view.tg_id,
        plan=view.plan_label,
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
        invite_link=view.invite_link,
        min_withdraw_usd=f"{view.min_withdraw_usd:.2f}",
    )


def _days_to_next_tier(
    status: UserStatus,
    progress_days: int,
    loyalty: SupportsLoyaltyThresholds | None = None,
) -> int | None:
    """Lived-coverage days remaining until the next loyalty tier, or None at the top tier."""
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
            return max(0, tier_threshold_days(active_months) - progress_days)
    return None
