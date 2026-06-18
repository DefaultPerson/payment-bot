from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards import CB_PROMO
from tests.test_handlers_user import (
    FakeCallback,
    FakeChat,
    FakeSession,
    FakeSessionFactory,
    FakeTelegramUser,
)


@dataclass(slots=True)
class FakePromoRecord:
    code: str


class FakeMessage:
    def __init__(self, *, text: str | None, from_user: FakeTelegramUser | None) -> None:
        self.text = text
        self.from_user = from_user
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


async def test_promo_command_stores_pending_promo(monkeypatch) -> None:
    import bot.handlers.promo as promo_module

    calls: list[dict[str, Any]] = []

    async def fake_apply_promo_code_for_user(
        session: FakeSession,
        *,
        tg_id: int,
        code: str,
        language_code: str | None,
        i18n: JsonI18n,
    ) -> FakePromoRecord:
        calls.append(
            {
                "session": session,
                "tg_id": tg_id,
                "code": code,
                "language_code": language_code,
                "i18n": i18n,
            }
        )
        return FakePromoRecord(code="SAVE20")

    monkeypatch.setattr(
        promo_module,
        "apply_promo_code_for_user",
        fake_apply_promo_code_for_user,
    )

    i18n = JsonI18n()
    session_factory = FakeSessionFactory()
    message = FakeMessage(
        text="/promo save20",
        from_user=FakeTelegramUser(id=700, language_code="en"),
    )

    await promo_module.promo_command(
        message,
        settings=Settings(),
        i18n=i18n,
        session_factory=session_factory,
    )

    assert calls == [
        {
            "session": session_factory.session,
            "tg_id": 700,
            "code": "save20",
            "language_code": "en",
            "i18n": i18n,
        }
    ]
    assert message.answers == [
        {"text": "Promo code SAVE20 will be applied to your next invoice."}
    ]


async def test_promo_command_without_code_replies_usage() -> None:
    import bot.handlers.promo as promo_module

    message = FakeMessage(
        text="/promo",
        from_user=FakeTelegramUser(id=700, language_code="en"),
    )

    await promo_module.promo_command(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert message.answers == [{"text": "Usage: /promo <code>"}]


async def test_promo_callback_prompts_for_command() -> None:
    import bot.handlers.promo as promo_module

    callback = FakeCallback(
        data=CB_PROMO,
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeChat(id=700),
    )

    await promo_module.promo_callback(callback, i18n=JsonI18n())

    assert callback.answers == [
        {"text": "Send /promo <code> to apply a promo code.", "show_alert": True}
    ]
