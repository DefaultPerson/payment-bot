from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.payments.cryptobot import CryptoBotInvoice
from bot.services.payments import PaidInvoiceResult


@dataclass(frozen=True, slots=True)
class InvoiceRef:
    id: int
    provider_invoice_id: str


class FakeSession:
    def __init__(self, factory: FakeSessionFactory) -> None:
        self.factory = factory
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True
        self.factory.events.append("commit")

    async def rollback(self) -> None:
        self.rolled_back = True
        self.factory.events.append("rollback")


class FakeSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self.events: list[str] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(self)
        self.sessions.append(session)
        return session


class FakeCryptoBotClient:
    def __init__(self, invoices: dict[str, CryptoBotInvoice]) -> None:
        self.invoices = invoices
        self.calls: list[dict[str, Any]] = []

    async def get_invoices(
        self,
        invoice_ids: list[str],
        status: str | None = None,
    ) -> list[CryptoBotInvoice]:
        self.calls.append({"invoice_ids": invoice_ids, "status": status})
        return [self.invoices[invoice_id] for invoice_id in invoice_ids]


class FailingCryptoBotClient:
    async def get_invoices(
        self,
        invoice_ids: list[str],
        status: str | None = None,
    ) -> list[CryptoBotInvoice]:
        raise RuntimeError("provider unavailable")


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


class FailingBot:
    async def send_message(self, **kwargs: Any) -> None:
        del kwargs
        raise RuntimeError("telegram unavailable")


class FakeServiceChannel:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.events.append("service-channel")
        self.messages.append(text)
        return True


class FailingServiceChannel:
    async def send_html(self, text: str) -> bool:
        del text
        return False


def make_provider_invoice(
    invoice_id: str,
    *,
    status: str,
    payload: str | None = None,
) -> CryptoBotInvoice:
    return CryptoBotInvoice(
        invoice_id=invoice_id,
        status=status,
        payload=payload or f"inv:{invoice_id}",
        pay_url=None,
        raw={"invoice_id": invoice_id, "status": status, "payload": payload or f"inv:{invoice_id}"},
    )


def paid_result(provider_invoice_id: str) -> PaidInvoiceResult:
    return PaidInvoiceResult(
        processed=True,
        invoice=object(),
        subscription=object(),
        invoice_id=22,
        provider_invoice_id=provider_invoice_id,
        user_tg_id=1001,
        lang_code="en",
        tariff_code="1m",
        gross=Decimal("73.30"),
        fee=Decimal("2.20"),
        net=Decimal("71.10"),
        currency="USD",
        subscription_expires_at=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_poll_once_fetches_active_invoices_in_batches_and_processes_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.jobs import crypto_poller

    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    active_invoices = [
        InvoiceRef(id=1, provider_invoice_id="provider-1"),
        InvoiceRef(id=2, provider_invoice_id="provider-2"),
    ]
    provider_events: list[str] = []
    processed: list[str] = []
    session_factory = FakeSessionFactory()
    bot = FakeBot()
    settings = Settings(
        bot={"support_username": "HydraSupport"},
        cryptobot={"poll_batch_size": 1},
        access={"protected_chats": []},
    )

    async def fake_get_active(
        session: FakeSession,
        *,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, now
        invoices = [
            invoice
            for invoice in active_invoices
            if after_id is None or invoice.id > after_id
        ]
        return invoices[:limit]

    async def fake_record_event(
        session: FakeSession,
        **kwargs: Any,
    ) -> None:
        del session
        provider_events.append(kwargs["external_id"])

    async def fake_process_paid(
        session: FakeSession,
        *,
        provider_invoice: CryptoBotInvoice,
        now: datetime,
        settings: Settings,
    ) -> PaidInvoiceResult:
        del session, now, settings
        processed.append(provider_invoice.invoice_id)
        return paid_result(provider_invoice.invoice_id)

    async def fake_mark_expired(session: FakeSession, *, now: datetime) -> int:
        del session, now
        return 0

    monkeypatch.setattr(crypto_poller, "get_active_cryptobot_invoices_after_id", fake_get_active)
    monkeypatch.setattr(crypto_poller, "record_provider_event", fake_record_event)
    monkeypatch.setattr(crypto_poller, "process_paid_cryptobot_invoice", fake_process_paid)
    monkeypatch.setattr(crypto_poller, "mark_expired_cryptobot_invoices", fake_mark_expired)

    result = await crypto_poller.poll_cryptobot_once(
        session_factory=session_factory,
        settings=settings,
        client=FakeCryptoBotClient(
            {
                "provider-1": make_provider_invoice("provider-1", status="active"),
                "provider-2": make_provider_invoice("provider-2", status="paid"),
            },
        ),
        bot=bot,
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel(session_factory.events),
        now=now,
    )

    assert result.polled == 2
    assert result.paid == 1
    assert result.expired == 0
    assert result.errors == 0
    assert processed == ["provider-2"]
    assert provider_events == ["provider-1", "provider-2"]
    assert "commit" in session_factory.events
    assert session_factory.events.index("commit") < session_factory.events.index(
        "service-channel",
    )
    assert bot.messages[0]["chat_id"] == 1001
    assert "Access chats are not configured" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_poll_once_marks_expired_local_invoices_after_provider_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.jobs import crypto_poller

    events: list[str] = []
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    async def fake_get_active(
        session: FakeSession,
        *,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, now, limit, after_id
        events.append("load-active")
        return [InvoiceRef(id=1, provider_invoice_id="provider-1")]

    async def fake_record_event(session: FakeSession, **kwargs: Any) -> None:
        del session, kwargs
        events.append("record-event")

    async def fake_mark_expired(session: FakeSession, *, now: datetime) -> int:
        del session, now
        events.append("mark-expired")
        return 2

    monkeypatch.setattr(crypto_poller, "get_active_cryptobot_invoices_after_id", fake_get_active)
    monkeypatch.setattr(crypto_poller, "record_provider_event", fake_record_event)
    monkeypatch.setattr(crypto_poller, "mark_expired_cryptobot_invoices", fake_mark_expired)

    result = await crypto_poller.poll_cryptobot_once(
        session_factory=FakeSessionFactory(),
        settings=Settings(cryptobot={"poll_batch_size": 10}),
        client=FakeCryptoBotClient(
            {"provider-1": make_provider_invoice("provider-1", status="active")},
        ),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel(events),
        now=now,
    )

    assert result.polled == 1
    assert result.expired == 2
    assert events == ["load-active", "record-event", "mark-expired"]


@pytest.mark.asyncio
async def test_poll_once_counts_provider_batch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.jobs import crypto_poller

    async def fake_get_active(
        session: FakeSession,
        *,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, now, limit, after_id
        return [InvoiceRef(id=1, provider_invoice_id="provider-1")]

    async def fake_mark_expired(session: FakeSession, *, now: datetime) -> int:
        del session, now
        return 0

    monkeypatch.setattr(crypto_poller, "get_active_cryptobot_invoices_after_id", fake_get_active)
    monkeypatch.setattr(crypto_poller, "mark_expired_cryptobot_invoices", fake_mark_expired)

    result = await crypto_poller.poll_cryptobot_once(
        session_factory=FakeSessionFactory(),
        settings=Settings(cryptobot={"poll_batch_size": 10}),
        client=FailingCryptoBotClient(),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel([]),
        now=datetime(2026, 6, 17, 12, 0, tzinfo=UTC),
    )

    assert result.polled == 0
    assert result.paid == 0
    assert result.errors == 1


@pytest.mark.asyncio
async def test_poll_once_counts_service_channel_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.jobs import crypto_poller

    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    async def fake_get_active(
        session: FakeSession,
        *,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, now, limit, after_id
        return [InvoiceRef(id=1, provider_invoice_id="provider-1")]

    async def fake_record_event(session: FakeSession, **kwargs: Any) -> None:
        del session, kwargs

    async def fake_process_paid(
        session: FakeSession,
        *,
        provider_invoice: CryptoBotInvoice,
        now: datetime,
        settings: Settings,
    ) -> PaidInvoiceResult:
        del session, now, settings
        return paid_result(provider_invoice.invoice_id)

    async def fake_mark_expired(session: FakeSession, *, now: datetime) -> int:
        del session, now
        return 0

    monkeypatch.setattr(crypto_poller, "get_active_cryptobot_invoices_after_id", fake_get_active)
    monkeypatch.setattr(crypto_poller, "record_provider_event", fake_record_event)
    monkeypatch.setattr(crypto_poller, "process_paid_cryptobot_invoice", fake_process_paid)
    monkeypatch.setattr(crypto_poller, "mark_expired_cryptobot_invoices", fake_mark_expired)

    result = await crypto_poller.poll_cryptobot_once(
        session_factory=FakeSessionFactory(),
        settings=Settings(cryptobot={"poll_batch_size": 10}, access={"protected_chats": []}),
        client=FakeCryptoBotClient(
            {"provider-1": make_provider_invoice("provider-1", status="paid")},
        ),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FailingServiceChannel(),
        now=now,
    )

    assert result.paid == 1
    assert result.errors == 1
    assert result.undelivered_access == ()


@pytest.mark.asyncio
async def test_poll_once_returns_access_delivery_retry_on_user_message_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.jobs import crypto_poller

    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    async def fake_get_active(
        session: FakeSession,
        *,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, now, limit, after_id
        return [InvoiceRef(id=1, provider_invoice_id="provider-1")]

    async def fake_record_event(session: FakeSession, **kwargs: Any) -> None:
        del session, kwargs

    async def fake_process_paid(
        session: FakeSession,
        *,
        provider_invoice: CryptoBotInvoice,
        now: datetime,
        settings: Settings,
    ) -> PaidInvoiceResult:
        del session, now, settings
        return paid_result(provider_invoice.invoice_id)

    async def fake_mark_expired(session: FakeSession, *, now: datetime) -> int:
        del session, now
        return 0

    monkeypatch.setattr(crypto_poller, "get_active_cryptobot_invoices_after_id", fake_get_active)
    monkeypatch.setattr(crypto_poller, "record_provider_event", fake_record_event)
    monkeypatch.setattr(crypto_poller, "process_paid_cryptobot_invoice", fake_process_paid)
    monkeypatch.setattr(crypto_poller, "mark_expired_cryptobot_invoices", fake_mark_expired)

    result = await crypto_poller.poll_cryptobot_once(
        session_factory=FakeSessionFactory(),
        settings=Settings(cryptobot={"poll_batch_size": 10}, access={"protected_chats": []}),
        client=FakeCryptoBotClient(
            {"provider-1": make_provider_invoice("provider-1", status="paid")},
        ),
        bot=FailingBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel([]),
        now=now,
    )

    assert result.paid == 1
    assert result.errors == 1
    assert [item.provider_invoice_id for item in result.undelivered_access] == ["provider-1"]


@pytest.mark.asyncio
async def test_poll_loop_retries_undelivered_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.jobs import crypto_poller

    retry_args: list[tuple[PaidInvoiceResult, ...]] = []
    pending = paid_result("provider-1")

    async def fake_poll_once(**kwargs: Any) -> crypto_poller.CryptoPollResult:
        retry_args.append(tuple(kwargs["retry_access"]))
        if len(retry_args) == 1:
            return crypto_poller.CryptoPollResult(
                polled=1,
                paid=1,
                expired=0,
                errors=1,
                undelivered_access=(pending,),
            )
        return crypto_poller.CryptoPollResult(polled=0, paid=0, expired=0, errors=0)

    async def no_sleep(delay: int) -> None:
        del delay

    monkeypatch.setattr(crypto_poller, "poll_cryptobot_once", fake_poll_once)

    await crypto_poller.crypto_poller_loop(
        session_factory=FakeSessionFactory(),
        settings=Settings(),
        client=FailingCryptoBotClient(),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel([]),
        sleep=no_sleep,
        max_iterations=2,
    )

    assert retry_args == [(), (pending,)]


@pytest.mark.asyncio
async def test_poll_loop_survives_iteration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.jobs import crypto_poller

    calls = 0

    async def failing_poll_once(**kwargs: Any) -> crypto_poller.CryptoPollResult:
        del kwargs
        nonlocal calls
        calls += 1
        raise RuntimeError("iteration failed")

    async def no_sleep(delay: int) -> None:
        del delay

    monkeypatch.setattr(crypto_poller, "poll_cryptobot_once", failing_poll_once)

    await crypto_poller.crypto_poller_loop(
        session_factory=FakeSessionFactory(),
        settings=Settings(),
        client=FailingCryptoBotClient(),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel([]),
        sleep=no_sleep,
        max_iterations=1,
    )

    assert calls == 1


@pytest.mark.asyncio
async def test_poll_loop_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.jobs import crypto_poller

    async def cancelled_poll_once(**kwargs: Any) -> crypto_poller.CryptoPollResult:
        del kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(crypto_poller, "poll_cryptobot_once", cancelled_poll_once)

    with pytest.raises(asyncio.CancelledError):
        await crypto_poller.crypto_poller_loop(
            session_factory=FakeSessionFactory(),
            settings=Settings(),
            client=FailingCryptoBotClient(),
            bot=FakeBot(),
            i18n=JsonI18n(fallback_language="en"),
            service_channel=FakeServiceChannel([]),
            max_iterations=1,
        )
