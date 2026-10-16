from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.models import InvoiceProvider
from bot.payments.xrocket import XRocketInvoice
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


class FakeXRocketClient:
    def __init__(self, invoices: dict[str, XRocketInvoice | Exception]) -> None:
        self.invoices = invoices
        self.calls: list[str] = []

    async def get_invoice(self, invoice_id: str) -> XRocketInvoice:
        self.calls.append(invoice_id)
        invoice = self.invoices[invoice_id]
        if isinstance(invoice, Exception):
            raise invoice
        return invoice


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


class FakeServiceChannel:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.events.append("service-channel")
        self.messages.append(text)
        return True


class FakeMetrics:
    def __init__(self) -> None:
        self.payments: list[tuple[str, str, Decimal | None]] = []
        self.provider_polls: list[tuple[str, str]] = []

    def record_payment(
        self,
        *,
        provider: str,
        status: str,
        amount_usd: Decimal | None = None,
    ) -> None:
        self.payments.append((provider, status, amount_usd))

    def record_provider_poll(self, *, provider: str, result: str) -> None:
        self.provider_polls.append((provider, result))


def make_provider_invoice(
    invoice_id: str,
    *,
    status: str,
    payload: str | None = None,
) -> XRocketInvoice:
    return XRocketInvoice(
        invoice_id=invoice_id,
        status=status,
        amount=Decimal("79.00"),
        currency="USDT",
        payload=payload or f"inv:{invoice_id}",
        pay_url=None,
        created_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        paid_at=None,
        raw={
            "id": invoice_id,
            "status": status,
            "payload": payload or f"inv:{invoice_id}",
            "currency": "USDT",
        },
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
        gross=Decimal("79.00"),
        fee=Decimal("0.00"),
        net=Decimal("79.00"),
        currency="USDT",
        subscription_expires_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_poll_once_fetches_active_invoices_individually_and_processes_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.jobs import xrocket_poller

    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    active_invoices = [
        InvoiceRef(id=1, provider_invoice_id="provider-1"),
        InvoiceRef(id=2, provider_invoice_id="provider-2"),
    ]
    provider_events: list[tuple[InvoiceProvider, str]] = []
    processed: list[str] = []
    session_factory = FakeSessionFactory()
    bot = FakeBot()
    metrics = FakeMetrics()
    settings = Settings(
        bot={"support_username": "HydraSupport"},
        xrocket={"poll_batch_size": 1},
        access={"protected_chats": []},
    )

    async def fake_get_active(
        session: FakeSession,
        *,
        provider: InvoiceProvider,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, now
        assert provider is InvoiceProvider.xrocket
        invoices = [
            invoice for invoice in active_invoices if after_id is None or invoice.id > after_id
        ]
        return invoices[:limit]

    async def fake_record_event(session: FakeSession, **kwargs: Any) -> None:
        del session
        provider_events.append((kwargs["provider"], kwargs["external_id"]))

    async def fake_process_paid(
        session: FakeSession,
        *,
        provider_invoice: XRocketInvoice,
        now: datetime,
        settings: Settings,
    ) -> PaidInvoiceResult:
        del session, now, settings
        processed.append(provider_invoice.invoice_id)
        return paid_result(provider_invoice.invoice_id)

    async def fake_mark_expired(
        session: FakeSession,
        *,
        provider: InvoiceProvider,
        now: datetime,
    ) -> int:
        del session, now
        assert provider is InvoiceProvider.xrocket
        return 0

    monkeypatch.setattr(xrocket_poller, "get_active_provider_invoices_after_id", fake_get_active)
    monkeypatch.setattr(xrocket_poller, "record_provider_event", fake_record_event)
    monkeypatch.setattr(xrocket_poller, "process_paid_xrocket_invoice", fake_process_paid)
    monkeypatch.setattr(xrocket_poller, "mark_expired_provider_invoices", fake_mark_expired)

    client = FakeXRocketClient(
        {
            "provider-1": make_provider_invoice("provider-1", status="active"),
            "provider-2": make_provider_invoice("provider-2", status="paid"),
        },
    )
    service_channel = FakeServiceChannel(session_factory.events)

    result = await xrocket_poller.poll_xrocket_once(
        session_factory=session_factory,
        settings=settings,
        client=client,
        bot=bot,
        i18n=JsonI18n(fallback_language="en"),
        service_channel=service_channel,
        metrics=metrics,
        now=now,
    )

    assert result.polled == 2
    assert result.paid == 1
    assert result.expired == 0
    assert result.errors == 0
    assert client.calls == ["provider-1", "provider-2"]
    assert processed == ["provider-2"]
    # active invoices are not recorded, only settled ones
    assert provider_events == [
        (InvoiceProvider.xrocket, "provider-2"),
    ]
    assert metrics.provider_polls == [("xrocket", "success"), ("xrocket", "success")]
    assert metrics.payments == [("xrocket", "paid", Decimal("79.00"))]
    assert "commit" in session_factory.events
    assert session_factory.events.index("commit") < session_factory.events.index(
        "service-channel",
    )
    assert "<b>Xrocket payment paid</b>" in service_channel.messages[0]
    assert bot.messages[0]["chat_id"] == 1001
    assert "Access chats are not configured" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_poll_once_counts_provider_fetch_errors_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.jobs import xrocket_poller

    async def fake_get_active(
        session: FakeSession,
        *,
        provider: InvoiceProvider,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, provider, now, limit, after_id
        return [
            InvoiceRef(id=1, provider_invoice_id="provider-1"),
            InvoiceRef(id=2, provider_invoice_id="provider-2"),
        ]

    async def fake_record_event(session: FakeSession, **kwargs: Any) -> None:
        del session, kwargs

    async def fake_mark_expired(
        session: FakeSession,
        *,
        provider: InvoiceProvider,
        now: datetime,
    ) -> int:
        del session, provider, now
        return 0

    monkeypatch.setattr(xrocket_poller, "get_active_provider_invoices_after_id", fake_get_active)
    monkeypatch.setattr(xrocket_poller, "record_provider_event", fake_record_event)
    monkeypatch.setattr(xrocket_poller, "mark_expired_provider_invoices", fake_mark_expired)
    metrics = FakeMetrics()

    result = await xrocket_poller.poll_xrocket_once(
        session_factory=FakeSessionFactory(),
        settings=Settings(xrocket={"poll_batch_size": 10}),
        client=FakeXRocketClient(
            {
                "provider-1": RuntimeError("provider unavailable"),
                "provider-2": make_provider_invoice("provider-2", status="active"),
            },
        ),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel([]),
        metrics=metrics,
        now=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
    )

    assert result.polled == 1
    assert result.paid == 0
    assert result.errors == 1
    assert metrics.provider_polls == [("xrocket", "error"), ("xrocket", "success")]


@pytest.mark.asyncio
async def test_poll_once_marks_expired_xrocket_invoices_after_provider_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.jobs import xrocket_poller

    events: list[str] = []

    async def fake_get_active(
        session: FakeSession,
        *,
        provider: InvoiceProvider,
        now: datetime,
        limit: int,
        after_id: int | None = None,
    ) -> list[InvoiceRef]:
        del session, now, limit, after_id
        assert provider is InvoiceProvider.xrocket
        events.append("load-active")
        return [InvoiceRef(id=1, provider_invoice_id="provider-1")]

    async def fake_record_event(session: FakeSession, **kwargs: Any) -> None:
        del session, kwargs
        events.append("record-event")

    async def fake_mark_expired(
        session: FakeSession,
        *,
        provider: InvoiceProvider,
        now: datetime,
    ) -> int:
        del session, now
        assert provider is InvoiceProvider.xrocket
        events.append("mark-expired")
        return 2

    monkeypatch.setattr(xrocket_poller, "get_active_provider_invoices_after_id", fake_get_active)
    monkeypatch.setattr(xrocket_poller, "record_provider_event", fake_record_event)
    monkeypatch.setattr(xrocket_poller, "mark_expired_provider_invoices", fake_mark_expired)

    result = await xrocket_poller.poll_xrocket_once(
        session_factory=FakeSessionFactory(),
        settings=Settings(xrocket={"poll_batch_size": 10}),
        client=FakeXRocketClient(
            {"provider-1": make_provider_invoice("provider-1", status="active")},
        ),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel(events),
        now=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
    )

    assert result.polled == 1
    assert result.expired == 2
    # active (unpaid) invoice → no provider event written, only the load + expiry sweep
    assert events == ["load-active", "mark-expired"]


@pytest.mark.asyncio
async def test_poll_loop_survives_iteration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.jobs import xrocket_poller

    calls = 0
    sleeps: list[int] = []

    async def fake_poll_once(**kwargs: Any) -> xrocket_poller.XRocketPollResult:
        del kwargs
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("iteration failed")
        return xrocket_poller.XRocketPollResult(polled=0, paid=0, expired=0, errors=0)

    async def no_sleep(delay: int) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(xrocket_poller, "poll_xrocket_once", fake_poll_once)

    await xrocket_poller.xrocket_poller_loop(
        session_factory=FakeSessionFactory(),
        settings=Settings(xrocket={"poll_interval_seconds": 7}),
        client=FakeXRocketClient({}),
        bot=FakeBot(),
        i18n=JsonI18n(fallback_language="en"),
        service_channel=FakeServiceChannel([]),
        sleep=no_sleep,
        max_iterations=2,
    )

    assert calls == 2
    assert sleeps == [7]


@pytest.mark.asyncio
async def test_poll_loop_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.jobs import xrocket_poller

    async def cancelled_poll_once(**kwargs: Any) -> xrocket_poller.XRocketPollResult:
        del kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(xrocket_poller, "poll_xrocket_once", cancelled_poll_once)

    with pytest.raises(asyncio.CancelledError):
        await xrocket_poller.xrocket_poller_loop(
            session_factory=FakeSessionFactory(),
            settings=Settings(),
            client=FakeXRocketClient({}),
            bot=FakeBot(),
            i18n=JsonI18n(fallback_language="en"),
            service_channel=FakeServiceChannel([]),
            max_iterations=1,
        )
