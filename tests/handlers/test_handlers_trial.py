from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

import bot.handlers.trial as trial_module
from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards import CB_TRIAL_CANCEL, CB_TRIAL_CONFIRM, CB_TRIAL_START
from bot.models import Subscription, SubscriptionSource, User
from bot.repos.subscriptions import extend_manual_subscription
from bot.repos.users import ensure_minimal_user
from tests.handlers.test_handlers_user import FakeCallback, FakeChat, FakeMessage, FakeTelegramUser

TG_ID = 7007


class FakeServiceChannel:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.events.append(text)
        return True


class FakeBot:
    """Placeholder bot — access delivery is stubbed out via monkeypatch."""


def _settings(**trial: Any) -> Settings:
    params: dict[str, Any] = {"enabled": True, "tariff_code": "1m", "duration_days": 3}
    params.update(trial)
    return Settings(trial=params)


def _callback(data: str, *, tg_id: int = TG_ID, lang: str = "en") -> FakeCallback:
    user = FakeTelegramUser(id=tg_id, language_code=lang)
    return FakeCallback(
        data=data,
        from_user=user,
        message=FakeMessage(from_user=user, chat=FakeChat(id=tg_id)),
    )


def _keyboard_callbacks(edit: dict[str, Any]) -> set[str]:
    return {
        button.callback_data
        for row in edit["reply_markup"].inline_keyboard
        for button in row
        if button.callback_data is not None
    }


async def _noop_send_access_links(*_args: Any, **_kwargs: Any) -> None:
    return None


async def test_trial_start_shows_confirmation(session_factory: Any) -> None:
    callback = _callback(CB_TRIAL_START)

    await trial_module.trial_start_callback(callback, _settings(), JsonI18n(), session_factory)

    assert callback.message.edits, "confirmation dialog should be shown"
    callbacks = _keyboard_callbacks(callback.message.edits[-1])
    assert {CB_TRIAL_CONFIRM, CB_TRIAL_CANCEL} <= callbacks


async def test_trial_start_alerts_when_not_eligible(session_factory: Any) -> None:
    async with session_factory() as session:
        user = await ensure_minimal_user(session, tg_id=TG_ID, i18n=JsonI18n())
        user.trial_used_at = datetime.now(UTC)
        await session.commit()

    callback = _callback(CB_TRIAL_START)
    await trial_module.trial_start_callback(callback, _settings(), JsonI18n(), session_factory)

    assert not callback.message.edits
    assert callback.answers and callback.answers[-1].get("show_alert") is True


async def test_trial_confirm_grants_delivers_and_notifies(
    session_factory: Any, monkeypatch: Any
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_send(bot: Any, settings: Any, i18n: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(trial_module, "send_access_links", fake_send)
    service_channel = FakeServiceChannel()
    callback = _callback(CB_TRIAL_CONFIRM)

    await trial_module.trial_confirm_callback(
        callback, _settings(), JsonI18n(), session_factory, FakeBot(), service_channel
    )

    assert calls and calls[-1]["plan_code"] == "main"
    assert calls[-1]["tg_id"] == TG_ID
    assert service_channel.events, "service channel should be notified"
    assert callback.message.edits, "success message should be shown"

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.tg_id == TG_ID))
        assert user is not None and user.trial_used_at is not None
        sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
        assert sub is not None and sub.source is SubscriptionSource.trial


async def test_trial_confirm_second_time_alerts_already_used(
    session_factory: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(trial_module, "send_access_links", _noop_send_access_links)
    settings = _settings()

    await trial_module.trial_confirm_callback(
        _callback(CB_TRIAL_CONFIRM),
        settings,
        JsonI18n(),
        session_factory,
        FakeBot(),
        FakeServiceChannel(),
    )

    second = _callback(CB_TRIAL_CONFIRM)
    await trial_module.trial_confirm_callback(
        second, settings, JsonI18n(), session_factory, FakeBot(), FakeServiceChannel()
    )

    assert second.answers and second.answers[-1].get("show_alert") is True
    assert not second.message.edits


async def test_trial_confirm_alerts_when_active_subscription(
    session_factory: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(trial_module, "send_access_links", _noop_send_access_links)
    async with session_factory() as session:
        user = await ensure_minimal_user(session, tg_id=TG_ID, i18n=JsonI18n())
        await extend_manual_subscription(session, user=user, days=30, now=datetime.now(UTC))
        await session.commit()

    callback = _callback(CB_TRIAL_CONFIRM)
    await trial_module.trial_confirm_callback(
        callback, _settings(), JsonI18n(), session_factory, FakeBot(), FakeServiceChannel()
    )

    assert callback.answers and callback.answers[-1].get("show_alert") is True
    assert not callback.message.edits


async def test_trial_cancel_returns_to_main_menu_with_trial_button() -> None:
    callback = _callback(CB_TRIAL_CANCEL)

    await trial_module.trial_cancel_callback(callback, _settings(), JsonI18n())

    assert callback.message.edits
    assert CB_TRIAL_START in _keyboard_callbacks(callback.message.edits[-1])
