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
from bot.models import InvoiceStatus, PromoKind, UserStatus
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotInvoice
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


@dataclass(slots=True)
class FakeTariffRecord:
    id: int


@dataclass(slots=True)
class FakeInvoiceRecord:
    id: int
    payload: str
    status: InvoiceStatus = InvoiceStatus.active
    provider_invoice_id: str | None = None


async def test_buy_callback_edits_tariff_list(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    calls: list[dict[str, Any]] = []

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        calls.append({"session": session, **kwargs})
        return FakeUserRecord(id=7, tg_id=700, lang_code="en")

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "tariff_keyboard", lambda *args: "tariff-keyboard")

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
    assert callback_message.edits == [
        {"text": "Choose a tariff:", "reply_markup": "tariff-keyboard"}
    ]


async def test_tariff_callback_edits_quote_detail(monkeypatch) -> None:
    import bot.handlers.buy as buy_module

    async def fake_ensure_minimal_user(session: FakeSession, **kwargs: Any) -> FakeUserRecord:
        return FakeUserRecord(id=7, tg_id=700, lang_code="en", status=UserStatus.basic)

    async def fake_pending_promo(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(buy_module, "ensure_minimal_user", fake_ensure_minimal_user)
    monkeypatch.setattr(buy_module, "get_pending_promo_snapshot_for_user", fake_pending_promo)
    monkeypatch.setattr(buy_module, "tariff_detail_keyboard", lambda *args: "detail-keyboard")

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
    assert "$219.90" in text
    assert "$213.30" in text
    assert callback.answers == [{"text": None}]
    assert callback_message.edits[0]["reply_markup"] == "detail-keyboard"


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

    assert "Promo SAVE20: -$15.80" in text


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
            "amount": Decimal("81.44"),
            "payload": "inv:00000000-0000-0000-0000-000000000001",
            "description": "Hydra Monitors access: 1 month",
            "paid_btn_url": "https://t.me/HydraSupport",
            "accepted_assets": ["USDT", "TON", "BTC"],
            "expires_in": 3600,
        }
    ]
    assert repo_calls[2]["name"] == "lock"
    assert repo_calls[2]["args"] == (session, 77)
    assert repo_calls[3]["name"] == "mark"
    assert repo_calls[3]["provider_invoice_id"] == "crypto-123"
    assert repo_calls[3]["raw_payload"] == provider_invoice.raw
    assert created.pay_url == "https://t.me/CryptoBot?start=pay"
    assert created.quote.gross_usd == Decimal("81.44")
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
