from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.services.referrals import ReferralBindResult, ReferralBindStatus
from tests.test_handlers_user import (
    FakeChat,
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
    referrer_id: int | None = None


@dataclass(slots=True)
class FakeUpsertResult:
    user: FakeUserRecord
    created: bool


async def test_start_binds_referral_payload_and_appends_feedback(monkeypatch) -> None:
    import bot.handlers.start as start_module

    bind_calls: list[dict[str, Any]] = []

    async def fake_upsert_user(session: FakeSession, **kwargs: Any) -> FakeUpsertResult:
        return FakeUpsertResult(FakeUserRecord(id=2, tg_id=2002, lang_code="en"), created=True)

    async def fake_bind_referral(session: FakeSession, **kwargs: Any) -> ReferralBindResult:
        bind_calls.append({"session": session, **kwargs})
        return ReferralBindResult(status=ReferralBindStatus.applied, referrer_tg_id=1001)

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fake_upsert_user)
    monkeypatch.setattr(start_module, "bind_referral_from_start", fake_bind_referral)
    monkeypatch.setattr(start_module, "set_user_commands", fake_set_user_commands)

    session_factory = FakeSessionFactory()
    message = FakeMessage(
        from_user=FakeTelegramUser(id=2002, username="referee", language_code="en"),
        chat=FakeChat(id=2002),
    )
    message.text = "/start ref_1001"

    await start_module.start(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=session_factory,
        bot=object(),
    )

    assert bind_calls[0]["session"] is session_factory.session
    assert bind_calls[0]["referee"].tg_id == 2002
    assert bind_calls[0]["referrer_tg_id"] == 1001
    assert bind_calls[0]["allow_referrer_change"] is False
    assert "Referral link applied." in message.answers[0]["text"]


async def test_start_reports_rebind_rejected_without_breaking_menu(monkeypatch) -> None:
    import bot.handlers.start as start_module

    async def fake_upsert_user(session: FakeSession, **kwargs: Any) -> FakeUpsertResult:
        return FakeUpsertResult(
            FakeUserRecord(id=2, tg_id=2002, lang_code="en", referrer_id=9),
            created=True,
        )

    async def fake_bind_referral(session: FakeSession, **kwargs: Any) -> ReferralBindResult:
        return ReferralBindResult(status=ReferralBindStatus.rebind_rejected, referrer_tg_id=1001)

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fake_upsert_user)
    monkeypatch.setattr(start_module, "bind_referral_from_start", fake_bind_referral)
    monkeypatch.setattr(start_module, "set_user_commands", fake_set_user_commands)

    message = FakeMessage(
        from_user=FakeTelegramUser(id=2002, language_code="en"),
        chat=FakeChat(id=2002),
    )
    message.text = "/start ref_1001"

    await start_module.start(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert "Referral is already set and cannot be changed." in message.answers[0]["text"]


async def test_start_without_referral_payload_does_not_call_bind(monkeypatch) -> None:
    import bot.handlers.start as start_module

    async def fake_upsert_user(session: FakeSession, **kwargs: Any) -> FakeUpsertResult:
        return FakeUpsertResult(FakeUserRecord(id=2, tg_id=2002, lang_code="en"), created=True)

    async def fail_bind_referral(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("regular /start must not bind referrals")

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fake_upsert_user)
    monkeypatch.setattr(start_module, "bind_referral_from_start", fail_bind_referral)
    monkeypatch.setattr(start_module, "set_user_commands", fake_set_user_commands)

    message = FakeMessage(
        from_user=FakeTelegramUser(id=2002, language_code="en"),
        chat=FakeChat(id=2002),
    )
    message.text = "/start"

    await start_module.start(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert "Referral link applied." not in message.answers[0]["text"]


async def test_start_existing_user_without_referrer_does_not_bind_late_payload(
    monkeypatch,
) -> None:
    import bot.handlers.start as start_module

    existing_user = FakeUserRecord(id=2, tg_id=2002, lang_code="en")

    async def fake_upsert_user(session: FakeSession, **kwargs: Any) -> FakeUpsertResult:
        return FakeUpsertResult(existing_user, created=False)

    async def fail_bind_referral(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("referrals must bind only on first /start")

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(start_module, "upsert_user_from_telegram_with_created", fake_upsert_user)
    monkeypatch.setattr(start_module, "bind_referral_from_start", fail_bind_referral)
    monkeypatch.setattr(start_module, "set_user_commands", fake_set_user_commands)

    message = FakeMessage(
        from_user=FakeTelegramUser(id=2002, language_code="en"),
        chat=FakeChat(id=2002),
    )
    message.text = "/start ref_1001"

    await start_module.start(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert "Referral link applied." not in message.answers[0]["text"]


async def test_start_does_not_bind_when_upsert_reports_conflict_created_elsewhere(
    monkeypatch,
) -> None:
    import bot.handlers.start as start_module

    existing_user = FakeUserRecord(id=2, tg_id=2002, lang_code="en")

    async def fake_upsert_with_created(session: FakeSession, **kwargs: Any) -> FakeUpsertResult:
        return FakeUpsertResult(user=existing_user, created=False)

    async def fail_legacy_upsert(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("start must use authoritative upsert result")

    async def fail_bind_referral(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("referrals must bind only when this /start created the user")

    async def fake_set_user_commands(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        start_module,
        "upsert_user_from_telegram_with_created",
        fake_upsert_with_created,
        raising=False,
    )
    monkeypatch.setattr(
        start_module,
        "upsert_user_from_telegram",
        fail_legacy_upsert,
        raising=False,
    )
    monkeypatch.setattr(start_module, "bind_referral_from_start", fail_bind_referral)
    monkeypatch.setattr(start_module, "set_user_commands", fake_set_user_commands)

    message = FakeMessage(
        from_user=FakeTelegramUser(id=2002, language_code="en"),
        chat=FakeChat(id=2002),
    )
    message.text = "/start ref_1001"

    await start_module.start(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
    )

    assert "Referral link applied." not in message.answers[0]["text"]
