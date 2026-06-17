from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from bot.models import (
    Invoice,
    InvoiceProvider,
    InvoiceStatus,
    ProviderEvent,
    User,
    UserStatus,
)
from bot.repos.invoices import (
    create_pending_cryptobot_invoice,
    get_active_cryptobot_invoices,
    get_active_cryptobot_invoices_after_id,
    lock_invoice_by_id,
    lock_invoice_by_provider_id,
    mark_expired_cryptobot_invoices,
    mark_invoice_paid,
    mark_invoice_provider_created,
)
from bot.repos.provider_events import record_provider_event


@dataclass(frozen=True, slots=True)
class Quote:
    gross_usd: Decimal = Decimal("73.30")
    fee_usd: Decimal = Decimal("2.20")
    net_usd: Decimal = Decimal("71.10")


class FakeScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def all(self) -> list[Any]:
        return self.items


class FakeAsyncSession:
    def __init__(self) -> None:
        self.invoices: list[Invoice] = []
        self.provider_events: list[ProviderEvent] = []
        self.flushed = 0
        self.committed = 0
        self.scalar_statements: list[Any] = []
        self.scalars_statements: list[Any] = []
        self._next_invoice_id = 1
        self._next_provider_event_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, Invoice) and instance not in self.invoices:
            self.invoices.append(instance)
        if isinstance(instance, ProviderEvent) and instance not in self.provider_events:
            self.provider_events.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for invoice in self.invoices:
            if invoice.id is None:
                invoice.id = self._next_invoice_id
                self._next_invoice_id += 1
        for event in self.provider_events:
            if event.id is None:
                event.id = self._next_provider_event_id
                self._next_provider_event_id += 1

    async def commit(self) -> None:
        self.committed += 1

    async def scalar(self, statement: Any) -> Any:
        self.scalar_statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params

        if entity is Invoice:
            invoice_id = params.get("id_1")
            if invoice_id is not None:
                return next(
                    (invoice for invoice in self.invoices if invoice.id == invoice_id),
                    None,
                )

            provider_invoice_id = next(
                (
                    value
                    for key, value in params.items()
                    if key.startswith("provider_invoice_id")
                ),
                None,
            )
            return next(
                (
                    invoice
                    for invoice in self.invoices
                    if invoice.provider is InvoiceProvider.cryptobot
                    and invoice.provider_invoice_id == provider_invoice_id
                ),
                None,
            )

        msg = f"unsupported fake scalar query for {entity}"
        raise AssertionError(msg)

    async def scalars(self, statement: Any) -> FakeScalarResult:
        self.scalars_statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params

        if entity is not Invoice:
            msg = f"unsupported fake scalars query for {entity}"
            raise AssertionError(msg)

        now = next(value for value in params.values() if isinstance(value, datetime))
        if statement._limit_clause is not None:
            limit = statement._limit_clause.value
            after_id = next(
                (
                    value
                    for key, value in params.items()
                    if key.startswith("id_") and isinstance(value, int)
                ),
                None,
            )
            invoices = [
                invoice
                for invoice in self.invoices
                if invoice.provider is InvoiceProvider.cryptobot
                and invoice.status is InvoiceStatus.active
                and invoice.provider_invoice_id is not None
                and invoice.expires_at > now
                and (after_id is None or (invoice.id is not None and invoice.id > after_id))
            ]
            return FakeScalarResult(
                sorted(invoices, key=lambda invoice: (invoice.id or 0, invoice.created_at))[
                    :limit
                ],
            )

        invoices = [
            invoice
            for invoice in self.invoices
            if invoice.provider is InvoiceProvider.cryptobot
            and invoice.status is InvoiceStatus.active
            and invoice.expires_at <= now
        ]
        return FakeScalarResult(invoices)


def make_user(*, user_id: int = 10) -> User:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return User(
        id=user_id,
        tg_id=1001,
        username=None,
        first_name=None,
        lang_code="ru",
        status=UserStatus.basic,
        total_active_days=0,
        referrer_id=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_invoice(
    *,
    invoice_id: int,
    provider: InvoiceProvider = InvoiceProvider.cryptobot,
    provider_invoice_id: str | None = "provider-1",
    status: InvoiceStatus = InvoiceStatus.active,
    expires_at: datetime,
    created_at: datetime | None = None,
) -> Invoice:
    timestamp = created_at or datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return Invoice(
        id=invoice_id,
        user_id=10,
        provider=provider,
        provider_invoice_id=provider_invoice_id,
        telegram_payment_charge_id=None,
        tariff_id=20,
        amount_usd=Decimal("73.30"),
        gross=Decimal("73.30"),
        fee=Decimal("2.20"),
        net=Decimal("71.10"),
        currency="USD",
        status=status,
        payload=f"inv:{invoice_id}",
        provider_payload=None,
        expires_at=expires_at,
        created_at=timestamp,
        paid_at=None,
    )


async def test_create_pending_invoice_sets_active_status_payload_and_amounts() -> None:
    session = FakeAsyncSession()
    user = make_user(user_id=44)
    expires_at = datetime(2026, 6, 17, 13, 0, tzinfo=UTC)

    invoice = await create_pending_cryptobot_invoice(
        session,
        user=user,
        tariff_id=55,
        quote=Quote(),
        expires_at=expires_at,
        payload="inv:abc",
    )

    assert invoice.id == 1
    assert invoice.user_id == 44
    assert invoice.tariff_id == 55
    assert invoice.provider is InvoiceProvider.cryptobot
    assert invoice.provider_invoice_id is None
    assert invoice.status is InvoiceStatus.active
    assert invoice.currency == "USD"
    assert invoice.amount_usd == Decimal("73.30")
    assert invoice.gross == Decimal("73.30")
    assert invoice.fee == Decimal("2.20")
    assert invoice.net == Decimal("71.10")
    assert invoice.payload == "inv:abc"
    assert invoice.expires_at == expires_at
    assert invoice in session.invoices
    assert session.flushed == 1
    assert session.committed == 0


async def test_mark_invoice_provider_created_persists_provider_id_and_payload() -> None:
    session = FakeAsyncSession()
    invoice = make_invoice(
        invoice_id=1,
        provider_invoice_id=None,
        expires_at=datetime(2026, 6, 17, 13, 0, tzinfo=UTC),
    )

    await mark_invoice_provider_created(
        session,
        invoice,
        provider_invoice_id="123",
        raw_payload={"invoice_id": 123},
    )

    assert invoice.provider_invoice_id == "123"
    assert invoice.provider_payload == {"invoice_id": 123}
    assert session.flushed == 1
    assert session.committed == 0


async def test_mark_invoice_provider_created_is_idempotent_for_same_provider_id() -> None:
    session = FakeAsyncSession()
    invoice = make_invoice(
        invoice_id=1,
        provider_invoice_id="123",
        expires_at=datetime(2026, 6, 17, 13, 0, tzinfo=UTC),
    )

    updated = await mark_invoice_provider_created(
        session,
        invoice,
        provider_invoice_id="123",
        raw_payload={"invoice_id": 123, "retry": True},
    )

    assert updated is invoice
    assert invoice.provider_invoice_id == "123"
    assert invoice.provider_payload == {"invoice_id": 123, "retry": True}
    assert session.flushed == 1


async def test_mark_invoice_provider_created_rejects_provider_id_remap() -> None:
    session = FakeAsyncSession()
    invoice = make_invoice(
        invoice_id=1,
        provider_invoice_id="123",
        expires_at=datetime(2026, 6, 17, 13, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="provider invoice id already set"):
        await mark_invoice_provider_created(
            session,
            invoice,
            provider_invoice_id="456",
            raw_payload={"invoice_id": 456},
        )

    assert invoice.provider_invoice_id == "123"
    assert session.flushed == 0


async def test_get_active_cryptobot_invoices_excludes_expired_and_missing_provider_ids() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    included_later_id = make_invoice(
        invoice_id=2,
        provider_invoice_id="2",
        expires_at=now + timedelta(minutes=5),
        created_at=now - timedelta(minutes=2),
    )
    included_earlier_id = make_invoice(
        invoice_id=1,
        provider_invoice_id="1",
        expires_at=now + timedelta(minutes=10),
        created_at=now - timedelta(minutes=1),
    )
    session.invoices.extend(
        [
            included_later_id,
            make_invoice(invoice_id=3, provider_invoice_id=None, expires_at=now + timedelta(1)),
            make_invoice(invoice_id=4, provider_invoice_id="4", expires_at=now),
            make_invoice(
                invoice_id=5,
                provider_invoice_id="5",
                status=InvoiceStatus.paid,
                expires_at=now + timedelta(1),
            ),
            make_invoice(
                invoice_id=6,
                provider=InvoiceProvider.stars,
                provider_invoice_id="6",
                expires_at=now + timedelta(1),
            ),
            included_earlier_id,
        ],
    )

    invoices = await get_active_cryptobot_invoices(session, now=now, limit=10)

    assert invoices == [included_earlier_id, included_later_id]
    statement = session.scalars_statements[0]
    assert statement._limit_clause.value == 10
    assert statement._order_by_clauses


async def test_get_active_cryptobot_invoices_preserves_first_page_behavior() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoices = [
        make_invoice(invoice_id=3, provider_invoice_id="3", expires_at=now + timedelta(1)),
        make_invoice(invoice_id=1, provider_invoice_id="1", expires_at=now + timedelta(1)),
        make_invoice(invoice_id=2, provider_invoice_id="2", expires_at=now + timedelta(1)),
    ]
    session.invoices.extend(invoices)

    first_page = await get_active_cryptobot_invoices(session, now=now, limit=2)

    assert [invoice.id for invoice in first_page] == [1, 2]


async def test_get_active_cryptobot_invoices_after_id_returns_ordered_batch_after_cursor() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    session.invoices.extend(
        [
            make_invoice(invoice_id=4, provider_invoice_id="4", expires_at=now + timedelta(1)),
            make_invoice(invoice_id=1, provider_invoice_id="1", expires_at=now + timedelta(1)),
            make_invoice(invoice_id=3, provider_invoice_id="3", expires_at=now + timedelta(1)),
            make_invoice(invoice_id=2, provider_invoice_id="2", expires_at=now + timedelta(1)),
            make_invoice(invoice_id=5, provider_invoice_id=None, expires_at=now + timedelta(1)),
        ],
    )

    batch = await get_active_cryptobot_invoices_after_id(
        session,
        now=now,
        limit=2,
        after_id=2,
    )

    assert [invoice.id for invoice in batch] == [3, 4]


async def test_lock_invoice_by_provider_id_uses_row_lock_and_returns_none_when_missing() -> None:
    session = FakeAsyncSession()
    invoice = make_invoice(
        invoice_id=7,
        provider_invoice_id="abc",
        expires_at=datetime(2026, 6, 17, 13, 0, tzinfo=UTC),
    )
    session.invoices.append(invoice)

    locked = await lock_invoice_by_provider_id(session, "abc")
    missing = await lock_invoice_by_provider_id(session, "missing")

    assert locked is invoice
    assert missing is None
    assert session.scalar_statements[0]._for_update_arg is not None
    assert session.scalar_statements[1]._for_update_arg is not None


async def test_lock_invoice_by_id_uses_row_lock() -> None:
    session = FakeAsyncSession()
    invoice = make_invoice(
        invoice_id=7,
        provider_invoice_id="abc",
        expires_at=datetime(2026, 6, 17, 13, 0, tzinfo=UTC),
    )
    session.invoices.append(invoice)

    locked = await lock_invoice_by_id(session, 7)

    assert locked is invoice
    assert session.scalar_statements[0]._for_update_arg is not None


async def test_mark_invoice_paid_sets_fields_and_preserves_existing_paid_at() -> None:
    session = FakeAsyncSession()
    first_paid_at = datetime(2026, 6, 17, 12, 30, tzinfo=UTC)
    later_paid_at = datetime(2026, 6, 17, 12, 35, tzinfo=UTC)
    invoice = make_invoice(
        invoice_id=8,
        expires_at=datetime(2026, 6, 17, 13, 0, tzinfo=UTC),
    )

    await mark_invoice_paid(
        session,
        invoice,
        paid_at=first_paid_at,
        raw_payload={"status": "paid"},
        gross=Decimal("74.00"),
        fee=Decimal("2.90"),
        net=Decimal("71.10"),
    )
    await mark_invoice_paid(
        session,
        invoice,
        paid_at=later_paid_at,
        raw_payload={"status": "paid", "duplicate": True},
    )

    assert invoice.status is InvoiceStatus.paid
    assert invoice.paid_at == first_paid_at
    assert invoice.provider_payload == {"status": "paid", "duplicate": True}
    assert invoice.gross == Decimal("74.00")
    assert invoice.fee == Decimal("2.90")
    assert invoice.net == Decimal("71.10")
    assert session.flushed == 2


async def test_mark_expired_cryptobot_invoices_marks_only_expired_active_invoices() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    expired = make_invoice(invoice_id=1, expires_at=now - timedelta(seconds=1))
    equal_deadline = make_invoice(invoice_id=2, expires_at=now)
    future = make_invoice(invoice_id=3, expires_at=now + timedelta(seconds=1))
    already_paid = make_invoice(
        invoice_id=4,
        status=InvoiceStatus.paid,
        expires_at=now - timedelta(seconds=1),
    )
    other_provider = make_invoice(
        invoice_id=5,
        provider=InvoiceProvider.stars,
        expires_at=now - timedelta(seconds=1),
    )
    session.invoices.extend([expired, equal_deadline, future, already_paid, other_provider])

    count = await mark_expired_cryptobot_invoices(session, now=now)

    assert count == 2
    assert expired.status is InvoiceStatus.expired
    assert equal_deadline.status is InvoiceStatus.expired
    assert future.status is InvoiceStatus.active
    assert already_paid.status is InvoiceStatus.paid
    assert other_provider.status is InvoiceStatus.active
    assert session.flushed == 1


async def test_record_provider_event_appends_audit_event_without_commit() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    event = await record_provider_event(
        session,
        provider=InvoiceProvider.cryptobot,
        external_id="123",
        raw_payload={"invoice_id": 123},
        now=now,
    )

    assert event.id == 1
    assert event.provider is InvoiceProvider.cryptobot
    assert event.external_id == "123"
    assert event.raw_payload == {"invoice_id": 123}
    assert event.received_at == now
    assert event.processed_at is None
    assert event in session.provider_events
    assert session.flushed == 1
    assert session.committed == 0
