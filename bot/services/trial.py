from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.models import User
from bot.repos.subscriptions import get_subscription_for_user, start_trial_subscription
from bot.repos.tariffs import default_plan, resolve_plan_code
from bot.repos.users import ensure_minimal_user
from bot.services.subscriptions import subscription_active_at

TrialResultStatus = Literal["activated", "already_used", "has_active", "disabled"]


@dataclass(frozen=True, slots=True)
class TrialResult:
    """Outcome of a trial activation, carrying everything the handler needs to deliver access
    and post the service-channel event without touching the ORM again."""

    status: TrialResultStatus
    user_tg_id: int | None = None
    username: str | None = None
    lang_code: str | None = None
    plan_code: str | None = None
    duration_days: int | None = None
    expires_at: datetime | None = None


async def is_trial_available(
    session: AsyncSession,
    *,
    user: User,
    settings: Settings,
    now: datetime,
) -> bool:
    """Whether ``user`` may start a trial: feature on, never used, no active subscription."""
    if not settings.trial.enabled:
        return False
    if user.trial_used_at is not None:
        return False
    subscription = await get_subscription_for_user(session, user.id)
    return not subscription_active_at(subscription.expires_at if subscription else None, now)


async def trial_availability(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    i18n: JsonI18n,
    *,
    tg_id: int | None,
    language_code: str | None,
    now: datetime,
) -> bool:
    """Session-opening convenience wrapper around :func:`is_trial_available` for handlers."""
    if not settings.trial.enabled or tg_id is None:
        return False
    async with session_scope(session_factory) as session:
        user = await ensure_minimal_user(
            session, tg_id=tg_id, i18n=i18n, language_code=language_code
        )
        return await is_trial_available(session, user=user, settings=settings, now=now)


async def activate_trial(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    i18n: JsonI18n,
    *,
    tg_id: int,
    language_code: str | None,
    now: datetime,
) -> TrialResult:
    """Grant the one-time trial subscription (idempotent) and return a delivery-ready result."""
    if not settings.trial.enabled:
        return TrialResult(status="disabled", user_tg_id=tg_id)

    async with session_scope(session_factory) as session:
        user = await ensure_minimal_user(
            session, tg_id=tg_id, i18n=i18n, language_code=language_code
        )
        plan_code = (
            resolve_plan_code(settings, settings.trial.tariff_code) or default_plan(settings).code
        )
        activation = await start_trial_subscription(
            session,
            user=user,
            days=settings.trial.duration_days,
            plan_code=plan_code,
            now=now,
            loyalty=settings.loyalty,
        )
        username = user.username
        lang_code = user.lang_code
        if activation.status != "activated" or activation.subscription is None:
            return TrialResult(
                status=activation.status,
                user_tg_id=tg_id,
                username=username,
                lang_code=lang_code,
            )
        subscription = activation.subscription
        return TrialResult(
            status="activated",
            user_tg_id=tg_id,
            username=username,
            lang_code=lang_code,
            plan_code=subscription.plan_code,
            duration_days=settings.trial.duration_days,
            expires_at=subscription.expires_at,
        )
