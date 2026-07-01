from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.models import Subscription, SubscriptionSource, User
from bot.repos.subscriptions import extend_manual_subscription
from bot.repos.users import ensure_minimal_user
from bot.services.trial import activate_trial, is_trial_available

TG_ID = 6001


def _settings(**trial: Any) -> Settings:
    params: dict[str, Any] = {"enabled": True, "tariff_code": "1m", "duration_days": 3}
    params.update(trial)
    return Settings(trial=params)


async def test_is_trial_available_true_for_fresh_eligible_user(session_factory: Any) -> None:
    settings = _settings()
    now = datetime.now(UTC)
    async with session_factory() as session:
        user = await ensure_minimal_user(session, tg_id=TG_ID, i18n=JsonI18n())
        assert await is_trial_available(session, user=user, settings=settings, now=now) is True


async def test_is_trial_available_false_when_disabled(session_factory: Any) -> None:
    settings = _settings(enabled=False)
    now = datetime.now(UTC)
    async with session_factory() as session:
        user = await ensure_minimal_user(session, tg_id=TG_ID, i18n=JsonI18n())
        assert await is_trial_available(session, user=user, settings=settings, now=now) is False


async def test_is_trial_available_false_when_already_used(session_factory: Any) -> None:
    settings = _settings()
    now = datetime.now(UTC)
    async with session_factory() as session:
        user = await ensure_minimal_user(session, tg_id=TG_ID, i18n=JsonI18n())
        user.trial_used_at = now
        await session.flush()
        assert await is_trial_available(session, user=user, settings=settings, now=now) is False


async def test_is_trial_available_false_with_active_subscription(session_factory: Any) -> None:
    settings = _settings()
    now = datetime.now(UTC)
    async with session_factory() as session:
        user = await ensure_minimal_user(session, tg_id=TG_ID, i18n=JsonI18n())
        await extend_manual_subscription(session, user=user, days=30, now=now)
        assert await is_trial_available(session, user=user, settings=settings, now=now) is False


async def test_activate_trial_grants_and_resolves_plan(session_factory: Any) -> None:
    settings = _settings(tariff_code="1m", duration_days=3)
    now = datetime.now(UTC)

    result = await activate_trial(
        session_factory, settings, JsonI18n(), tg_id=TG_ID, language_code="en", now=now
    )

    assert result.status == "activated"
    assert result.plan_code == "main"
    assert result.duration_days == 3
    assert result.expires_at == now + timedelta(days=3)

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.tg_id == TG_ID))
        assert user is not None
        assert user.trial_used_at is not None
        sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
        assert sub is not None
        assert sub.source is SubscriptionSource.trial


async def test_activate_trial_is_idempotent(session_factory: Any) -> None:
    settings = _settings()
    now = datetime.now(UTC)

    first = await activate_trial(
        session_factory, settings, JsonI18n(), tg_id=TG_ID, language_code="en", now=now
    )
    assert first.status == "activated"

    second = await activate_trial(
        session_factory,
        settings,
        JsonI18n(),
        tg_id=TG_ID,
        language_code="en",
        now=now + timedelta(days=1),
    )
    assert second.status == "already_used"


async def test_activate_trial_disabled_returns_disabled(session_factory: Any) -> None:
    settings = _settings(enabled=False)
    now = datetime.now(UTC)

    result = await activate_trial(
        session_factory, settings, JsonI18n(), tg_id=TG_ID, language_code="en", now=now
    )

    assert result.status == "disabled"
