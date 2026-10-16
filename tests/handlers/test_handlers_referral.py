from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.repos.referrals import ReferralStats
from tests.handlers.test_handlers_user import (
    FakeCallback,
    FakeChat,
    FakeInaccessibleMessage,
    FakeMessage,
    FakeSession,
    FakeSessionFactory,
    FakeTelegramUser,
)


@dataclass(slots=True)
class FakeUserRecord:
    id: int
    tg_id: int
    lang_code: str


async def test_referral_command_answers_invite_link_and_stats(monkeypatch) -> None:
    import bot.handlers.referral as referral_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en")

    async def fake_stats(session: FakeSession, *, referrer_id: int) -> ReferralStats:
        assert referrer_id == 7
        return ReferralStats(
            referral_count=3,
            earned_usd=Decimal("21.50"),
            unpaid_earned_usd=Decimal("11.50"),
        )

    monkeypatch.setattr(referral_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(referral_module, "get_referral_stats", fake_stats)

    message = FakeMessage(
        from_user=FakeTelegramUser(id=700, language_code="en"),
        chat=FakeChat(id=700),
    )

    await referral_module.referral(
        message,
        settings=Settings(bot={"username": "HydraPayBot"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    text = message.answers[0]["text"]
    assert "https://t.me/HydraPayBot?start=ref_700" in text
    assert "Referrals: 3" in text
    assert "Lifetime earned: $21.50" in text
    assert "Available for withdrawal: $11.50 of $10.00 minimum" in text


async def test_referral_callback_edits_message(monkeypatch) -> None:
    import bot.handlers.referral as referral_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en")

    async def fake_stats(session: FakeSession, *, referrer_id: int) -> ReferralStats:
        return ReferralStats(
            referral_count=0,
            earned_usd=Decimal("0.00"),
            unpaid_earned_usd=Decimal("0.00"),
        )

    monkeypatch.setattr(referral_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(referral_module, "get_referral_stats", fake_stats)

    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="referral",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await referral_module.referral_callback(
        callback,
        settings=Settings(bot={"username": "HydraPayBot"}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert "https://t.me/HydraPayBot?start=ref_700" in callback_message.edits[0]["text"]
    assert callback.answers == [{"text": None}]


async def test_referral_callback_fallback_truncates_long_alert_text() -> None:
    import bot.handlers.referral as referral_module

    callback = FakeCallback(
        data="referral",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeInaccessibleMessage(chat=FakeChat(id=700)),
    )

    await referral_module._edit_text_or_answer(callback, "Referral\n\n" + ("details " * 80))

    answer_text = callback.answers[0]["text"]
    assert answer_text.startswith("Referral")
    assert answer_text.endswith("...")
    assert len(answer_text) <= 200
