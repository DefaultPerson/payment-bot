from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.models import UserStatus
from bot.repos.referrals import InsufficientReferralBalance
from tests.test_handlers_user import (
    FakeCallback,
    FakeChat,
    FakeMessage,
    FakeSessionFactory,
    FakeTelegramUser,
)

EXPIRES = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class FakeServiceChannel:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return True


def _make_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=7, tg_id=700, lang_code="en", status=UserStatus.basic, username="bob"
    )


def _patch_common(
    monkeypatch: Any,
    buy_module: Any,
    *,
    spend: Any,
    extend: Any,
    access_calls: list[dict[str, Any]],
) -> None:
    async def fake_ensure_minimal_user(session: Any, **kwargs: Any) -> SimpleNamespace:
        return _make_user()

    def fake_get_tariff(settings: Any, code: str) -> SimpleNamespace:
        return SimpleNamespace(duration_months=1, code=code)

    def fake_quote_tariff(settings: Any, code: str, status: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(net_usd=Decimal("10.00"))

    def fake_resolve_plan_code(settings: Any, code: str) -> str:
        return "main"

    async def fake_send_access(bot: Any, settings: Any, i18n: Any, **kwargs: Any) -> None:
        access_calls.append(kwargs)

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "get_tariff", fake_get_tariff)
    monkeypatch.setattr(buy_module, "quote_tariff", fake_quote_tariff)
    monkeypatch.setattr(buy_module, "resolve_plan_code", fake_resolve_plan_code)
    monkeypatch.setattr(buy_module, "spend_referral_balance", spend)
    monkeypatch.setattr(buy_module, "extend_manual_subscription", extend)
    monkeypatch.setattr(buy_module, "send_access_links", fake_send_access)


def _callback() -> FakeCallback:
    return FakeCallback(
        data="pay:referral:3m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=700)),
    )


async def test_referral_pay_grants_debits_notifies_and_delivers(monkeypatch: Any) -> None:
    import bot.handlers.buy as buy_module

    spent: dict[str, Any] = {}
    granted: dict[str, Any] = {}
    access_calls: list[dict[str, Any]] = []

    async def fake_spend(session: Any, *, user: Any, amount: Decimal) -> Decimal:
        spent["amount"] = amount
        return Decimal("5.00")

    async def fake_extend(
        session: Any,
        *,
        user: Any,
        days: int,
        now: Any,
        loyalty: Any,
        plan_code: Any,
    ) -> SimpleNamespace:
        granted.update(days=days, plan_code=plan_code)
        return SimpleNamespace(expires_at=EXPIRES)

    _patch_common(
        monkeypatch, buy_module, spend=fake_spend, extend=fake_extend, access_calls=access_calls
    )

    callback = _callback()
    service_channel = FakeServiceChannel()

    await buy_module.referral_pay_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
        service_channel=service_channel,
    )

    assert spent["amount"] == Decimal("10.00")
    assert granted == {"days": 30, "plan_code": "main"}
    assert len(service_channel.calls) == 1
    assert "Referral balance purchase" in service_channel.calls[0]
    assert len(access_calls) == 1
    assert access_calls[0]["plan_code"] == "main"
    assert callback.message.edits  # success message shown to the buyer


async def test_referral_pay_insufficient_balance_does_not_grant(monkeypatch: Any) -> None:
    import bot.handlers.buy as buy_module

    granted: list[Any] = []
    access_calls: list[dict[str, Any]] = []

    async def fake_spend(session: Any, *, user: Any, amount: Decimal) -> Decimal:
        raise InsufficientReferralBalance(Decimal("3.00"))

    async def fake_extend(session: Any, **kwargs: Any) -> SimpleNamespace:
        granted.append(kwargs)
        return SimpleNamespace(expires_at=EXPIRES)

    _patch_common(
        monkeypatch, buy_module, spend=fake_spend, extend=fake_extend, access_calls=access_calls
    )

    callback = _callback()
    service_channel = FakeServiceChannel()

    await buy_module.referral_pay_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
        service_channel=service_channel,
    )

    assert granted == []  # no subscription granted
    assert service_channel.calls == []  # no event
    assert access_calls == []  # no access links
    assert callback.answers  # an alert was shown
    assert callback.answers[0].get("show_alert") is True


async def test_referral_pay_survives_service_channel_failure(monkeypatch: Any) -> None:
    import bot.handlers.buy as buy_module

    access_calls: list[dict[str, Any]] = []

    async def fake_spend(session: Any, *, user: Any, amount: Decimal) -> Decimal:
        return Decimal("0.00")

    async def fake_extend(
        session: Any,
        *,
        user: Any,
        days: int,
        now: Any,
        loyalty: Any,
        plan_code: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(expires_at=EXPIRES)

    _patch_common(
        monkeypatch, buy_module, spend=fake_spend, extend=fake_extend, access_calls=access_calls
    )

    callback = _callback()
    service_channel = FakeServiceChannel(error=RuntimeError("channel down"))

    await buy_module.referral_pay_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
        service_channel=service_channel,
    )

    # Grant + access delivery still complete even though the service-channel send raised.
    assert len(access_calls) == 1
    assert callback.message.edits
