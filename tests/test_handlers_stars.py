from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.services.payments import PaidInvoiceResult, StarsPreCheckoutResult
from tests.test_handlers_user import FakeSession, FakeSessionFactory, FakeTelegramUser
from tests.test_services_payments import make_stars_invoice


@dataclass(slots=True)
class FakePreCheckoutQuery:
    invoice_payload: str
    currency: str
    total_amount: int
    from_user: FakeTelegramUser
    answers: list[dict[str, Any]]

    async def answer(self, *, ok: bool, error_message: str | None = None) -> None:
        self.answers.append({"ok": ok, "error_message": error_message})


class FakeSuccessfulPayment:
    def __init__(
        self,
        *,
        invoice_payload: str = "stars:payload",
        currency: str = "XTR",
        total_amount: int = 8682,
        telegram_payment_charge_id: str = "charge-1",
    ) -> None:
        self.invoice_payload = invoice_payload
        self.currency = currency
        self.total_amount = total_amount
        self.telegram_payment_charge_id = telegram_payment_charge_id

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {
            "invoice_payload": self.invoice_payload,
            "currency": self.currency,
            "total_amount": self.total_amount,
            "telegram_payment_charge_id": self.telegram_payment_charge_id,
        }


class FakeMessage:
    def __init__(self) -> None:
        self.from_user = FakeTelegramUser(id=700, language_code="en")
        self.successful_payment = FakeSuccessfulPayment()
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


class FakeServiceChannel:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.messages.append(text)
        return True


class FakeMetrics:
    def __init__(self) -> None:
        self.payments: list[tuple[str, str, Decimal | None]] = []

    def record_payment(
        self,
        *,
        provider: str,
        status: str,
        amount_usd: Decimal | None = None,
    ) -> None:
        self.payments.append((provider, status, amount_usd))


async def test_pre_checkout_query_answers_ok(monkeypatch) -> None:
    import bot.handlers.stars as stars_module

    calls: list[dict[str, Any]] = []

    async def fake_validate(session: FakeSession, **kwargs: Any) -> StarsPreCheckoutResult:
        calls.append({"session": session, **kwargs})
        return StarsPreCheckoutResult(ok=True, invoice=object())

    monkeypatch.setattr(stars_module, "validate_stars_pre_checkout", fake_validate)

    query = FakePreCheckoutQuery(
        invoice_payload="stars:payload",
        currency="XTR",
        total_amount=8682,
        from_user=FakeTelegramUser(id=700, language_code="en"),
        answers=[],
    )
    session_factory = FakeSessionFactory()

    await stars_module.pre_checkout_query(
        query,
        i18n=JsonI18n(),
        session_factory=session_factory,
    )

    assert calls[0]["session"] is session_factory.session
    assert calls[0]["payload"] == "stars:payload"
    assert calls[0]["currency"] == "XTR"
    assert calls[0]["total_amount"] == 8682
    assert calls[0]["now"].tzinfo is not None
    assert query.answers == [{"ok": True, "error_message": None}]


async def test_pre_checkout_query_answers_rejection(monkeypatch) -> None:
    import bot.handlers.stars as stars_module

    async def fake_validate(session: FakeSession, **kwargs: Any) -> StarsPreCheckoutResult:
        del session, kwargs
        return StarsPreCheckoutResult(ok=False, error_message="invoice amount mismatch")

    monkeypatch.setattr(stars_module, "validate_stars_pre_checkout", fake_validate)

    query = FakePreCheckoutQuery(
        invoice_payload="stars:payload",
        currency="XTR",
        total_amount=1,
        from_user=FakeTelegramUser(id=700, language_code="en"),
        answers=[],
    )

    await stars_module.pre_checkout_query(
        query,
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert query.answers == [{"ok": False, "error_message": "invoice amount mismatch"}]


async def test_successful_payment_processes_invoice_and_sends_access(monkeypatch) -> None:
    import bot.handlers.stars as stars_module

    access_calls: list[dict[str, Any]] = []

    async def fake_process(session: FakeSession, **kwargs: Any) -> PaidInvoiceResult:
        del session, kwargs
        invoice = make_stars_invoice()
        return PaidInvoiceResult(
            processed=True,
            invoice=invoice,
            subscription=object(),
            invoice_id=55,
            user_tg_id=700,
            lang_code="en",
            tariff_code="1m",
            gross=Decimal("81.44"),
            fee=Decimal("2.44"),
            net=Decimal("79.00"),
            currency="XTR",
            subscription_expires_at=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        )

    async def fake_send_access_links(
        bot: object,
        settings: Settings,
        i18n: JsonI18n,
        **kwargs: Any,
    ) -> None:
        access_calls.append({"bot": bot, "settings": settings, "i18n": i18n, **kwargs})

    monkeypatch.setattr(stars_module, "process_paid_stars_invoice", fake_process)
    monkeypatch.setattr(stars_module, "send_access_links", fake_send_access_links)

    settings = Settings(access={"protected_chats": []})
    i18n = JsonI18n()
    service_channel = FakeServiceChannel()
    message = FakeMessage()
    bot = object()
    metrics = FakeMetrics()

    await stars_module.successful_payment(
        message,
        settings=settings,
        i18n=i18n,
        session_factory=FakeSessionFactory(),
        bot=bot,
        service_channel=service_channel,
        metrics=metrics,
    )

    assert access_calls[0]["bot"] is bot
    assert access_calls[0]["tg_id"] == 700
    assert access_calls[0]["lang_code"] == "en"
    assert service_channel.messages
    answer = message.answers[0]
    assert answer["text"] == "Payment received. Your access has been activated."
    assert answer["reply_markup"].inline_keyboard[0][0].callback_data == "dismiss"  # ❌ close
    assert metrics.payments == [("stars", "paid", Decimal("81.44"))]


async def test_successful_payment_answers_generic_when_processing_rejects(
    monkeypatch,
) -> None:
    import bot.handlers.stars as stars_module

    async def fake_process(session: FakeSession, **kwargs: Any) -> PaidInvoiceResult:
        del session, kwargs
        msg = "invoice amount mismatch"
        raise ValueError(msg)

    monkeypatch.setattr(stars_module, "process_paid_stars_invoice", fake_process)

    message = FakeMessage()
    metrics = FakeMetrics()

    await stars_module.successful_payment(
        message,
        settings=Settings(access={"protected_chats": []}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
        service_channel=FakeServiceChannel(),
        metrics=metrics,
    )

    assert message.answers == [{"text": "Something went wrong. Please try again later."}]
    assert metrics.payments == [("stars", "error", None)]


async def test_successful_payment_keeps_access_retryable_when_delivery_fails(
    monkeypatch,
) -> None:
    import bot.handlers.stars as stars_module

    mark_calls: list[dict[str, Any]] = []

    async def fake_process(session: FakeSession, **kwargs: Any) -> PaidInvoiceResult:
        del session, kwargs
        return PaidInvoiceResult(
            processed=True,
            invoice=make_stars_invoice(),
            subscription=object(),
            invoice_id=55,
            user_tg_id=700,
            lang_code="en",
            tariff_code="1m",
            gross=Decimal("112.87"),
            fee=Decimal("33.86"),
            net=Decimal("79.01"),
            currency="USD",
        )

    async def failing_send_access_links(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        msg = "telegram delivery failed"
        raise RuntimeError(msg)

    async def fake_mark_access_delivered(**kwargs: Any) -> None:
        mark_calls.append(kwargs)

    monkeypatch.setattr(stars_module, "process_paid_stars_invoice", fake_process)
    monkeypatch.setattr(stars_module, "send_access_links", failing_send_access_links)
    monkeypatch.setattr(
        stars_module,
        "_mark_access_delivered",
        fake_mark_access_delivered,
        raising=False,
    )

    message = FakeMessage()

    await stars_module.successful_payment(
        message,
        settings=Settings(access={"protected_chats": []}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=object(),
        service_channel=FakeServiceChannel(),
    )

    assert mark_calls == []
    assert message.answers == [{"text": "Something went wrong. Please try again later."}]


async def test_successful_payment_retries_access_for_idempotent_undelivered_charge(
    monkeypatch,
) -> None:
    import bot.handlers.stars as stars_module

    access_calls: list[dict[str, Any]] = []
    mark_calls: list[dict[str, Any]] = []

    async def fake_process(session: FakeSession, **kwargs: Any) -> PaidInvoiceResult:
        del session, kwargs
        return PaidInvoiceResult(
            processed=False,
            invoice=make_stars_invoice(status=object()),
            subscription=object(),
            invoice_id=55,
            user_tg_id=700,
            lang_code="en",
            tariff_code="1m",
            gross=Decimal("112.87"),
            fee=Decimal("33.86"),
            net=Decimal("79.01"),
            currency="USD",
        )

    async def fake_send_access_links(
        bot: object,
        settings: Settings,
        i18n: JsonI18n,
        **kwargs: Any,
    ) -> None:
        access_calls.append({"bot": bot, "settings": settings, "i18n": i18n, **kwargs})

    async def fake_mark_access_delivered(**kwargs: Any) -> None:
        mark_calls.append(kwargs)

    monkeypatch.setattr(stars_module, "process_paid_stars_invoice", fake_process)
    monkeypatch.setattr(stars_module, "send_access_links", fake_send_access_links)
    monkeypatch.setattr(
        stars_module,
        "_mark_access_delivered",
        fake_mark_access_delivered,
        raising=False,
    )

    message = FakeMessage()
    bot = object()
    metrics = FakeMetrics()

    await stars_module.successful_payment(
        message,
        settings=Settings(access={"protected_chats": []}),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
        bot=bot,
        service_channel=FakeServiceChannel(),
        metrics=metrics,
    )

    assert access_calls[0]["bot"] is bot
    assert access_calls[0]["tg_id"] == 700
    assert mark_calls[0]["invoice_id"] == 55
    answer = message.answers[0]
    assert answer["text"] == "Payment received. Your access has been activated."
    assert answer["reply_markup"].inline_keyboard[0][0].callback_data == "dismiss"  # ❌ close
    assert metrics.payments == [("stars", "duplicate", None)]
