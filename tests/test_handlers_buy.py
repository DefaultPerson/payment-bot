from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards import CB_STARS_PREFIX
from bot.models import InvoiceStatus, PromoKind, UserStatus
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotInvoice
from bot.payments.xrocket import XRocketAPIError
from bot.services.pricing import PromoSnapshot, quote_tariff
from tests.test_handlers_user import (
    FakeBadRequestMessage,
    FakeCallback,
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
    status: UserStatus = UserStatus.basic
    total_active_days: int = 0


@dataclass(slots=True)
class FakeTariffRecord:
    id: int


@dataclass(slots=True)
class FakeInvoiceRecord:
    id: int
    payload: str
    status: InvoiceStatus = InvoiceStatus.active
    provider_invoice_id: str | None = None


@dataclass(slots=True)
class FakeStarsQuote:
    amount: int
    currency: str = "XTR"
    subscription_period: int | None = 2_592_000


@dataclass(slots=True)
class FakeCreatedStarsInvoice:
    invoice: object
    quote: object
    stars: FakeStarsQuote
    payload: str
    title: str
    description: str


async def test_buy_callback_edits_tariff_list(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    calls: list[dict[str, Any]] = []

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        calls.append({"session": session, **kwargs})
        return FakeUserRecord(id=7, tg_id=700, lang_code="en")

    async def fake_no_subscription(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "get_subscription_for_user", fake_no_subscription)
    monkeypatch.setattr(buy_module, "plan_keyboard", lambda *args: "plan-keyboard")

    i18n = JsonI18n()
    session_factory = FakeSessionFactory()
    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="buy",
        from_user=FakeTelegramUser(id=700, language_code="uk"),
        message=callback_message,
    )

    await buy_module.buy_callback(
        callback,
        settings=Settings(),
        i18n=i18n,
        session_factory=session_factory,
    )

    assert calls == [
        {
            "session": session_factory.session,
            "tg_id": 700,
            "i18n": i18n,
            "language_code": "uk",
        }
    ]
    assert callback.answers == [{"text": None}]
    # Menu 1 — tariff (product) selection with the user's current plan (Free without a sub).
    assert callback_message.edits == [
        {
            "text": "<b>Choose a tariff</b>\n\n<b>Current:</b> <code>Free</code>",
            "reply_markup": "plan-keyboard",
        }
    ]


async def test_plan_callback_shows_duration_menu(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_build_duration_menu(*args: Any, **kwargs: Any) -> tuple[str, str, str]:
        assert kwargs["plan_code"] == "main"
        return "duration-text", "duration-keyboard", "en"

    monkeypatch.setattr(buy_module, "build_duration_menu", fake_build_duration_menu)

    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="plan:main",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.plan_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    # Menu 2 — duration selection for the chosen plan.
    assert callback_message.edits == [
        {"text": "duration-text", "reply_markup": "duration-keyboard"}
    ]


async def test_back_to_tariffs_callback_shows_plan_menu(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en")

    async def fake_no_subscription(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "get_subscription_for_user", fake_no_subscription)
    monkeypatch.setattr(buy_module, "plan_keyboard", lambda *args: "plan-keyboard")

    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="back:tariffs",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.back_to_tariffs_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback_message.edits == [
        {
            "text": "<b>Choose a tariff</b>\n\n<b>Current:</b> <code>Free</code>",
            "reply_markup": "plan-keyboard",
        }
    ]


async def test_priced_tariffs_applies_loyalty_and_promo(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_menu_promo(
        session: FakeSession, *, user: Any, now: Any
    ) -> tuple[PromoSnapshot, frozenset[str]]:
        snapshot = PromoSnapshot(
            promo_id=1, code="SAVE20", kind=PromoKind.percent, value=Decimal("20")
        )
        return snapshot, frozenset()  # empty allowed_tariffs -> applies to all

    monkeypatch.setattr(buy_module, "get_pending_promo_for_menu", fake_menu_promo)

    priced = await buy_module._priced_tariffs(
        FakeSession(),
        Settings(),
        "main",
        FakeUserRecord(id=7, tg_id=700, lang_code="en"),
        UserStatus.basic,
        datetime(2026, 7, 3, tzinfo=UTC),
    )

    by_code = {entry.tariff.code: entry for entry in priced}
    one_month = by_code["1m"]
    # 1m base $59.00, no duration/loyalty discount; a 20% promo knocks off $11.80 -> $47.20.
    assert one_month.new_usd == Decimal("47.20")
    assert one_month.promo_discount_usd == Decimal("11.80")
    # Invariant the keyboard relies on to strike the pre-promo price.
    assert one_month.old_usd == one_month.new_usd + one_month.promo_discount_usd


async def test_priced_tariffs_restricts_promo_to_allowed_tariffs(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_menu_promo(
        session: FakeSession, *, user: Any, now: Any
    ) -> tuple[PromoSnapshot, frozenset[str]]:
        snapshot = PromoSnapshot(
            promo_id=1, code="SAVE6M", kind=PromoKind.percent, value=Decimal("20")
        )
        return snapshot, frozenset({"6m"})  # restricted to the 6-month tariff only

    monkeypatch.setattr(buy_module, "get_pending_promo_for_menu", fake_menu_promo)

    priced = await buy_module._priced_tariffs(
        FakeSession(),
        Settings(),
        "main",
        FakeUserRecord(id=7, tg_id=700, lang_code="en"),
        UserStatus.basic,
        datetime(2026, 7, 3, tzinfo=UTC),
    )

    by_code = {entry.tariff.code: entry for entry in priced}
    # Promo applies only to its allowed tariff; other buttons keep the regular price (no strike).
    assert by_code["6m"].promo_discount_usd > Decimal("0.00")
    assert by_code["1m"].promo_discount_usd == Decimal("0.00")
    assert by_code["1m"].old_usd == by_code["1m"].new_usd


async def test_tariff_callback_edits_quote_detail(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fake_menu_promo(*args: Any, **kwargs: Any) -> tuple[None, frozenset[str]]:
        return None, frozenset()

    async def fake_referral_stats(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(available_usd=Decimal("0"))

    def fake_detail_keyboard(*args: Any, **kwargs: Any) -> str:
        return "detail-keyboard"

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "get_pending_promo_for_menu", fake_menu_promo)
    monkeypatch.setattr(buy_module, "get_referral_stats", fake_referral_stats)
    monkeypatch.setattr(buy_module, "tariff_detail_keyboard", fake_detail_keyboard)

    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="tariff:3m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.tariff_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    text = callback_message.edits[0]["text"]
    assert "3 months" in text
    assert "USDT, TON, BTC" in text
    assert "$173.35" in text
    assert "$168.15" in text
    assert callback.answers == [{"text": None}]
    assert callback_message.edits[0]["reply_markup"] == "detail-keyboard"


async def test_tariff_callback_gates_promo_to_allowed_tariffs(monkeypatch) -> None:
    # A pending promo restricted to '6m' must NOT price down an unrelated tariff's quote, and
    # (via the non-destructive resolver) viewing that quote must not consume the promo.
    import bot.handlers.buy as buy_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en")

    async def fake_menu_promo(*args: Any, **kwargs: Any) -> tuple[PromoSnapshot, frozenset[str]]:
        snapshot = PromoSnapshot(
            promo_id=1, code="SAVE6M", kind=PromoKind.percent, value=Decimal("20")
        )
        return snapshot, frozenset({"6m"})

    async def fake_referral_stats(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(available_usd=Decimal("0"))

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "get_pending_promo_for_menu", fake_menu_promo)
    monkeypatch.setattr(buy_module, "get_referral_stats", fake_referral_stats)
    monkeypatch.setattr(buy_module, "tariff_detail_keyboard", lambda *a, **k: "detail-keyboard")

    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="tariff:3m",  # not the promo's allowed tariff
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.tariff_callback(
        callback, settings=Settings(), i18n=JsonI18n(), session_factory=FakeSessionFactory()
    )

    text = callback_message.edits[0]["text"]
    # Full 3m price ($168.15 net), no promo line applied.
    assert "$168.15" in text
    assert "SAVE6M" not in text


async def test_stars_invoice_callback_sends_telegram_stars_invoice(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    service_calls: list[dict[str, Any]] = []

    class InvoiceMessage(FakeMessage):
        def __init__(self, *, chat: FakeChat) -> None:
            super().__init__(chat=chat)
            self.invoices: list[dict[str, Any]] = []

        async def answer_invoice(self, **kwargs: Any) -> None:
            self.invoices.append(kwargs)

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fake_create_stars_invoice(
        session_factory_arg: FakeSessionFactory,
        settings: Settings,
        user: FakeUserRecord,
        tariff_code: str,
        now: datetime,
        i18n: JsonI18n,
    ) -> FakeCreatedStarsInvoice:
        service_calls.append(
            {
                "session_factory": session_factory_arg,
                "settings": settings,
                "user": user,
                "tariff_code": tariff_code,
                "now": now,
                "i18n": i18n,
            }
        )
        return FakeCreatedStarsInvoice(
            invoice=object(),
            quote=object(),
            stars=FakeStarsQuote(amount=8682, subscription_period=2_592_000),
            payload="stars:payload",
            title="Hydra Monitors",
            description="Hydra Monitors access: 1 month",
        )

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_stars_invoice_for_user", fake_create_stars_invoice)

    settings = Settings(stars={"enabled": True})
    session_factory = FakeSessionFactory()
    callback_message = InvoiceMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data=f"{CB_STARS_PREFIX}1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.stars_invoice_callback(
        callback,
        settings=settings,
        i18n=JsonI18n(),
        session_factory=session_factory,
    )

    assert service_calls[0]["session_factory"] is session_factory
    assert service_calls[0]["tariff_code"] == "1m"
    invoice_call = callback_message.invoices[0]
    assert invoice_call["title"] == "Hydra Monitors"
    assert invoice_call["description"] == "Hydra Monitors access: 1 month"
    assert invoice_call["payload"] == "stars:payload"
    assert invoice_call["currency"] == "XTR"
    assert invoice_call["provider_token"] == ""
    assert invoice_call["prices"][0].amount == 8682
    assert invoice_call["subscription_period"] == 2_592_000
    assert callback.answers == [{"text": None}]


async def test_stars_invoice_callback_cancels_local_invoice_when_telegram_send_fails(
    monkeypatch,
) -> None:
    import bot.handlers.buy as buy_module

    cancelled_invoice_ids: list[int] = []

    class FailingInvoiceMessage(FakeMessage):
        async def answer_invoice(self, **kwargs: Any) -> None:
            del kwargs
            msg = "telegram send failed"
            raise RuntimeError(msg)

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fake_create_stars_invoice(
        session_factory_arg: FakeSessionFactory,
        settings: Settings,
        user: FakeUserRecord,
        tariff_code: str,
        now: datetime,
        i18n: JsonI18n,
    ) -> FakeCreatedStarsInvoice:
        del session_factory_arg, settings, user, tariff_code, now, i18n
        return FakeCreatedStarsInvoice(
            invoice=SimpleNamespace(id=42),
            quote=object(),
            stars=FakeStarsQuote(amount=8682, subscription_period=2_592_000),
            payload="stars:payload",
            title="Hydra Monitors",
            description="Hydra Monitors access: 1 month",
        )

    async def fake_cancel_unusable_invoice(
        session_factory_arg: FakeSessionFactory,
        invoice_id: int,
    ) -> None:
        del session_factory_arg
        cancelled_invoice_ids.append(invoice_id)

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_stars_invoice_for_user", fake_create_stars_invoice)
    monkeypatch.setattr(buy_module, "cancel_unusable_invoice", fake_cancel_unusable_invoice)

    callback = FakeCallback(
        data=f"{CB_STARS_PREFIX}1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FailingInvoiceMessage(chat=FakeChat(id=700)),
    )

    await buy_module.stars_invoice_callback(
        callback,
        settings=Settings(stars={"enabled": True}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert cancelled_invoice_ids == [42]
    assert callback.answers == [
        {
            "text": "Could not create invoice. Please try again or contact support.",
            "show_alert": True,
        }
    ]


def test_format_quote_text_includes_promo_discount() -> None:
    import bot.handlers.buy as buy_module

    quote = quote_tariff(
        Settings(),
        "1m",
        UserStatus.basic,
        promo=PromoSnapshot(
            promo_id=5,
            code="SAVE20",
            kind=PromoKind.percent,
            value=Decimal("20"),
        ),
    )

    text = buy_module._format_quote_text(JsonI18n(), Settings(), "en", quote)

    assert "Promo SAVE20: -$11.80" in text


async def test_cryptobot_invoice_callback_returns_payment_url(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    service_calls: list[dict[str, Any]] = []
    keyboard_calls: list[dict[str, Any]] = []

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fake_create_invoice(
        session_factory_arg: FakeSessionFactory,
        settings: Settings,
        client: object,
        user: FakeUserRecord,
        tariff_code: str,
        paid_btn_url: str,
        now: datetime,
    ) -> SimpleNamespace:
        service_calls.append(
            {
                "session_factory": session_factory_arg,
                "settings": settings,
                "client": client,
                "user": user,
                "tariff_code": tariff_code,
                "paid_btn_url": paid_btn_url,
                "now": now,
            }
        )
        return SimpleNamespace(
            pay_url="https://t.me/CryptoBot?start=pay",
            quote=SimpleNamespace(gross_usd=Decimal("81.44")),
            invoice=object(),
        )

    def fake_payment_keyboard(
        i18n: JsonI18n,
        settings: Settings,
        lang_code: str,
        pay_url: str,
    ) -> str:
        keyboard_calls.append(
            {"i18n": i18n, "settings": settings, "lang_code": lang_code, "pay_url": pay_url}
        )
        return "payment-keyboard"

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_cryptobot_invoice_for_user", fake_create_invoice)
    monkeypatch.setattr(buy_module, "payment_keyboard", fake_payment_keyboard)

    settings = Settings(bot={"support_username": "HydraSupport"})
    client = object()
    session_factory = FakeSessionFactory()
    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="pay:cryptobot:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.cryptobot_invoice_callback(
        callback,
        settings=settings,
        i18n=JsonI18n(),
        session_factory=session_factory,
        cryptobot_client=client,
    )

    assert service_calls[0]["session_factory"] is session_factory
    assert service_calls[0]["settings"] is settings
    assert service_calls[0]["client"] is client
    assert service_calls[0]["tariff_code"] == "1m"
    assert service_calls[0]["paid_btn_url"] == "https://t.me/HydraSupport"
    assert service_calls[0]["now"].tzinfo is not None
    assert keyboard_calls == [
        {
            "i18n": keyboard_calls[0]["i18n"],
            "settings": settings,
            "lang_code": "en",
            "pay_url": "https://t.me/CryptoBot?start=pay",
        }
    ]
    assert session_factory.session.commits == 1
    assert callback.answers == [{"text": None}]
    assert callback_message.edits == [
        {
            "text": (
                "Invoice created. Use the payment button below to complete your purchase.\n"
                "https://t.me/CryptoBot?start=pay"
            ),
            "reply_markup": "payment-keyboard",
        }
    ]


async def test_cryptobot_invoice_callback_allows_missing_injected_client() -> None:
    import bot.handlers.buy as buy_module

    callback = FakeCallback(
        data="pay:cryptobot:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=700)),
    )

    await buy_module.cryptobot_invoice_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [
        {
            "text": "Could not create invoice. Please try again or contact support.",
            "show_alert": True,
        }
    ]


async def test_xrocket_invoice_callback_returns_payment_url(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    service_calls: list[dict[str, Any]] = []
    keyboard_calls: list[dict[str, Any]] = []

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fake_create_invoice(
        session_factory_arg: FakeSessionFactory,
        settings: Settings,
        client: object,
        user: FakeUserRecord,
        tariff_code: str,
        now: datetime,
    ) -> SimpleNamespace:
        service_calls.append(
            {
                "session_factory": session_factory_arg,
                "settings": settings,
                "client": client,
                "user": user,
                "tariff_code": tariff_code,
                "now": now,
            }
        )
        return SimpleNamespace(
            pay_url="https://t.me/xrocket?start=pay",
            quote=SimpleNamespace(gross_usd=Decimal("79.00")),
            invoice=object(),
        )

    def fake_payment_keyboard(
        i18n: JsonI18n,
        settings: Settings,
        lang_code: str,
        pay_url: str,
        *,
        payment_key: str = "buy.pay.cryptobot",
    ) -> str:
        keyboard_calls.append(
            {
                "i18n": i18n,
                "settings": settings,
                "lang_code": lang_code,
                "pay_url": pay_url,
                "payment_key": payment_key,
            }
        )
        return "payment-keyboard"

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_xrocket_invoice_for_user", fake_create_invoice)
    monkeypatch.setattr(buy_module, "payment_keyboard", fake_payment_keyboard)

    settings = Settings(xrocket={"enabled": True})
    client = object()
    session_factory = FakeSessionFactory()
    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="pay:xrocket:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.xrocket_invoice_callback(
        callback,
        settings=settings,
        i18n=JsonI18n(),
        session_factory=session_factory,
        xrocket_client=client,
    )

    assert service_calls[0]["session_factory"] is session_factory
    assert service_calls[0]["settings"] is settings
    assert service_calls[0]["client"] is client
    assert service_calls[0]["tariff_code"] == "1m"
    assert service_calls[0]["now"].tzinfo is not None
    assert keyboard_calls == [
        {
            "i18n": keyboard_calls[0]["i18n"],
            "settings": settings,
            "lang_code": "en",
            "pay_url": "https://t.me/xrocket?start=pay",
            "payment_key": "buy.pay.xrocket",
        }
    ]
    assert session_factory.session.commits == 1
    assert callback.answers == [{"text": None}]
    assert callback_message.edits == [
        {
            "text": (
                "Invoice created. Use the payment button below to complete your purchase.\n"
                "https://t.me/xrocket?start=pay"
            ),
            "reply_markup": "payment-keyboard",
        }
    ]


async def test_xrocket_invoice_callback_allows_missing_injected_client() -> None:
    import bot.handlers.buy as buy_module

    callback = FakeCallback(
        data="pay:xrocket:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=700)),
    )

    await buy_module.xrocket_invoice_callback(
        callback,
        settings=Settings(xrocket={"enabled": True}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert callback.answers == [
        {
            "text": "Could not create invoice. Please try again or contact support.",
            "show_alert": True,
        }
    ]


async def test_xrocket_invoice_callback_provider_errors_do_not_send_pay_url(
    monkeypatch,
) -> None:
    import bot.handlers.buy as buy_module

    payment_keyboard_called = False

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fail_create_invoice(*args: Any, **kwargs: Any) -> None:
        raise XRocketAPIError("TOKEN_INVALID")

    def fail_payment_keyboard(*args: Any, **kwargs: Any) -> None:
        nonlocal payment_keyboard_called
        payment_keyboard_called = True

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_xrocket_invoice_for_user", fail_create_invoice)
    monkeypatch.setattr(buy_module, "payment_keyboard", fail_payment_keyboard)

    session_factory = FakeSessionFactory()
    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="pay:xrocket:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.xrocket_invoice_callback(
        callback,
        settings=Settings(xrocket={"enabled": True}),
        i18n=JsonI18n(),
        session_factory=session_factory,
        xrocket_client=object(),
    )

    assert session_factory.session.commits == 1
    assert payment_keyboard_called is False
    assert callback.answers == [
        {
            "text": "Could not create invoice. Please try again or contact support.",
            "show_alert": True,
        }
    ]
    assert callback_message.edits == []


async def test_cryptobot_invoice_callback_surfaces_service_database_errors(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fail_create_invoice(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_cryptobot_invoice_for_user", fail_create_invoice)

    session_factory = FakeSessionFactory()
    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="pay:cryptobot:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.cryptobot_invoice_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=session_factory,
        cryptobot_client=object(),
    )

    assert session_factory.session.commits == 1
    assert callback.answers == [
        {"text": "Something went wrong. Please try again later.", "show_alert": True}
    ]
    assert callback_message.edits == []


async def test_cryptobot_invoice_callback_provider_errors_do_not_send_pay_url(
    monkeypatch,
) -> None:
    import bot.handlers.buy as buy_module

    payment_keyboard_called = False

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fail_create_invoice(*args: Any, **kwargs: Any) -> None:
        raise CryptoBotAPIError("TOKEN_INVALID")

    def fail_payment_keyboard(*args: Any, **kwargs: Any) -> None:
        nonlocal payment_keyboard_called
        payment_keyboard_called = True

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_cryptobot_invoice_for_user", fail_create_invoice)
    monkeypatch.setattr(buy_module, "payment_keyboard", fail_payment_keyboard)

    session_factory = FakeSessionFactory()
    callback_message = FakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data="pay:cryptobot:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=callback_message,
    )

    await buy_module.cryptobot_invoice_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=session_factory,
        cryptobot_client=object(),
    )

    assert session_factory.session.commits == 1
    assert payment_keyboard_called is False
    assert callback.answers == [
        {
            "text": "Could not create invoice. Please try again or contact support.",
            "show_alert": True,
        }
    ]
    assert callback_message.edits == []


async def test_cryptobot_invoice_callback_alert_fallback_includes_payment_url(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fake_create_invoice(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            pay_url="https://t.me/CryptoBot?start=pay",
            quote=SimpleNamespace(gross_usd=Decimal("81.44")),
            invoice=object(),
        )

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_cryptobot_invoice_for_user", fake_create_invoice)
    monkeypatch.setattr(buy_module, "payment_keyboard", lambda *args: "payment-keyboard")

    callback = FakeCallback(
        data="pay:cryptobot:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeBadRequestMessage(chat=FakeChat(id=700)),
    )

    await buy_module.cryptobot_invoice_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        cryptobot_client=object(),
    )

    assert callback.answers == [
        {
            "text": (
                "Invoice created. Use the payment button below to complete your purchase.\n"
                "https://t.me/CryptoBot?start=pay"
            ),
            "show_alert": True,
        }
    ]


async def test_cryptobot_invoice_callback_transport_errors_do_not_bubble(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fail_create_invoice(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("network timeout")

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "create_cryptobot_invoice_for_user", fail_create_invoice)

    callback = FakeCallback(
        data="pay:cryptobot:1m",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=700)),
    )

    await buy_module.cryptobot_invoice_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        cryptobot_client=object(),
    )

    assert callback.answers == [
        {
            "text": "Could not create invoice. Please try again or contact support.",
            "show_alert": True,
        }
    ]


async def test_bad_cryptobot_callback_data_answers_generic_alert(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fail_create_invoice(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("invalid callback data must not create an invoice")

    monkeypatch.setattr(buy_module, "create_cryptobot_invoice_for_user", fail_create_invoice)

    callback = FakeCallback(
        data="pay:cryptobot:",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=700)),
    )

    await buy_module.cryptobot_invoice_callback(
        callback,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        cryptobot_client=object(),
    )

    assert callback.answers == [
        {"text": "Something went wrong. Please try again later.", "show_alert": True}
    ]


async def test_bad_xrocket_callback_data_answers_generic_alert(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fail_create_invoice(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("invalid callback data must not create an invoice")

    monkeypatch.setattr(buy_module, "create_xrocket_invoice_for_user", fail_create_invoice)

    callback = FakeCallback(
        data="pay:xrocket:",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeMessage(chat=FakeChat(id=700)),
    )

    await buy_module.xrocket_invoice_callback(
        callback,
        settings=Settings(xrocket={"enabled": True}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        xrocket_client=object(),
    )

    assert callback.answers == [
        {"text": "Something went wrong. Please try again later.", "show_alert": True}
    ]


async def test_create_cryptobot_invoice_for_user_commits_before_provider_call(monkeypatch) -> None:
    import bot.services.payments as payments_module

    settings = Settings(bot={"support_username": "HydraSupport"})
    user = FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)
    session = FakeSession()
    session_factory = FakeSessionFactory()
    session_factory.session = session
    client_calls: list[dict[str, Any]] = []
    repo_calls: list[dict[str, Any]] = []
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    provider_invoice = CryptoBotInvoice(
        invoice_id="crypto-123",
        status="active",
        payload="inv:00000000-0000-0000-0000-000000000001",
        pay_url="https://t.me/CryptoBot?start=pay",
        raw={"invoice_id": 123, "bot_invoice_url": "https://t.me/CryptoBot?start=pay"},
    )

    class FakeClient:
        async def create_invoice(self, **kwargs: Any) -> CryptoBotInvoice:
            client_calls.append({"commits_before_provider": session.commits})
            client_calls.append(kwargs)
            return provider_invoice

    async def fake_ensure_configured_tariff(*args: Any, **kwargs: Any) -> FakeTariffRecord:
        repo_calls.append({"name": "tariff", "args": args, **kwargs})
        return FakeTariffRecord(id=55)

    async def fake_create_pending_invoice(*args: Any, **kwargs: Any) -> FakeInvoiceRecord:
        repo_calls.append({"name": "pending", "args": args, **kwargs})
        return FakeInvoiceRecord(id=77, payload=kwargs["payload"])

    async def fake_get_pending_promo_for_user(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[FakeUserRecord, None]:
        return user, None

    async def fake_mark_provider_created(*args: Any, **kwargs: Any) -> FakeInvoiceRecord:
        repo_calls.append({"name": "mark", "args": args, **kwargs})
        return args[1]

    async def fake_lock_invoice_by_id(*args: Any, **kwargs: Any) -> FakeInvoiceRecord:
        repo_calls.append({"name": "lock", "args": args, **kwargs})
        return FakeInvoiceRecord(id=77, payload="inv:00000000-0000-0000-0000-000000000001")

    monkeypatch.setattr(payments_module.uuid, "uuid4", lambda: UUID(int=1))
    monkeypatch.setattr(
        payments_module,
        "ensure_configured_tariff",
        fake_ensure_configured_tariff,
    )
    monkeypatch.setattr(
        payments_module,
        "create_pending_cryptobot_invoice",
        fake_create_pending_invoice,
    )
    monkeypatch.setattr(
        payments_module,
        "get_pending_promo_for_user",
        fake_get_pending_promo_for_user,
    )
    monkeypatch.setattr(
        payments_module,
        "mark_invoice_provider_created",
        fake_mark_provider_created,
    )
    monkeypatch.setattr(payments_module, "lock_invoice_by_id", fake_lock_invoice_by_id)

    created = await payments_module.create_cryptobot_invoice_for_user(
        session_factory,
        settings,
        FakeClient(),
        user,
        "1m",
        "https://t.me/HydraSupport",
        now,
    )

    assert repo_calls[0]["name"] == "tariff"
    assert repo_calls[0]["args"] == (session, settings, "1m")
    assert repo_calls[1]["name"] == "pending"
    assert repo_calls[1]["args"] == (session,)
    assert repo_calls[1]["user"] is user
    assert repo_calls[1]["tariff_id"] == 55
    assert repo_calls[1]["expires_at"] == now + timedelta(seconds=3600)
    assert repo_calls[1]["payload"] == "inv:00000000-0000-0000-0000-000000000001"
    assert client_calls == [
        {"commits_before_provider": 1},
        {
            "amount": Decimal("60.82"),
            "payload": "inv:00000000-0000-0000-0000-000000000001",
            "description": "Hydra Monitors access: 1 month",
            "paid_btn_url": "https://t.me/HydraSupport",
            "accepted_assets": ["USDT", "TON", "BTC"],
            "expires_in": 3600,
        },
    ]
    assert repo_calls[2]["name"] == "lock"
    assert repo_calls[2]["args"] == (session, 77)
    assert repo_calls[3]["name"] == "mark"
    assert repo_calls[3]["provider_invoice_id"] == "crypto-123"
    assert repo_calls[3]["raw_payload"] == provider_invoice.raw
    assert created.pay_url == "https://t.me/CryptoBot?start=pay"
    assert created.quote.gross_usd == Decimal("60.82")
    assert session.commits == 2


async def test_create_cryptobot_invoice_for_user_rejects_missing_provider_pay_url(
    monkeypatch,
) -> None:
    import bot.services.payments as payments_module

    provider_invoice = CryptoBotInvoice(
        invoice_id="crypto-123",
        status="active",
        payload="payload",
        pay_url=None,
        raw={"invoice_id": 123},
    )
    mark_calls: list[object] = []

    class FakeClient:
        async def create_invoice(self, **kwargs: Any) -> CryptoBotInvoice:
            return provider_invoice

    async def fake_ensure_configured_tariff(*args: Any, **kwargs: Any) -> FakeTariffRecord:
        return FakeTariffRecord(id=55)

    async def fake_create_pending_invoice(*args: Any, **kwargs: Any) -> FakeInvoiceRecord:
        return FakeInvoiceRecord(id=77, payload=kwargs["payload"])

    async def fake_get_pending_promo_for_user(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[FakeUserRecord, None]:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic), None

    async def fake_mark_provider_created(*args: Any, **kwargs: Any) -> None:
        mark_calls.append(args)

    async def fake_lock_invoice_by_id(*args: Any, **kwargs: Any) -> FakeInvoiceRecord:
        return FakeInvoiceRecord(id=77, payload="payload")

    monkeypatch.setattr(
        payments_module,
        "ensure_configured_tariff",
        fake_ensure_configured_tariff,
    )
    monkeypatch.setattr(
        payments_module,
        "create_pending_cryptobot_invoice",
        fake_create_pending_invoice,
    )
    monkeypatch.setattr(
        payments_module,
        "get_pending_promo_for_user",
        fake_get_pending_promo_for_user,
    )
    monkeypatch.setattr(
        payments_module,
        "mark_invoice_provider_created",
        fake_mark_provider_created,
    )
    monkeypatch.setattr(payments_module, "lock_invoice_by_id", fake_lock_invoice_by_id)

    session_factory = FakeSessionFactory()
    with pytest.raises(CryptoBotAPIError, match="missing pay URL"):
        await payments_module.create_cryptobot_invoice_for_user(
            session_factory,
            Settings(),
            FakeClient(),
            FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic),
            "1m",
            "https://t.me/HydraSupport",
            datetime(2026, 6, 17, 12, 0, tzinfo=UTC),
        )

    assert mark_calls == []


async def test_cancel_unusable_invoice_releases_reserved_promo(monkeypatch) -> None:
    import bot.services.payments as payments_module

    invoice = SimpleNamespace(
        id=77,
        status=InvoiceStatus.active,
        provider_invoice_id=None,
        promo_id=5,
    )
    release_calls: list[object] = []

    async def fake_lock_invoice_by_id(*args: Any, **kwargs: Any) -> object:
        return invoice

    async def fake_release_promo_redemption_for_invoice(*args: Any, **kwargs: Any) -> None:
        release_calls.append(args[1])

    monkeypatch.setattr(payments_module, "lock_invoice_by_id", fake_lock_invoice_by_id)
    monkeypatch.setattr(
        payments_module,
        "release_promo_redemption_for_invoice",
        fake_release_promo_redemption_for_invoice,
    )

    session_factory = FakeSessionFactory()
    await payments_module._cancel_unusable_invoice(session_factory, 77)

    assert release_calls == [invoice]
    assert invoice.status is InvoiceStatus.cancelled
    assert session_factory.session.commits == 1
