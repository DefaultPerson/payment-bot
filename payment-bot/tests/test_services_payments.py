from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from bot.models import (
    Invoice,
    InvoiceProvider,
    InvoiceStatus,
    PromoKind,
    Subscription,
    SubscriptionSource,
    Tariff,
    User,
    UserStatus,
)
from bot.payments.cryptobot import CryptoBotInvoice
from bot.services.payments import (
    StarsSuccessfulPayment,
    process_paid_cryptobot_invoice,
    process_paid_stars_invoice,
    validate_stars_pre_checkout,
)


class FakeAsyncSession:
    def __init__(self) -> None:
        self.invoices: list[Invoice] = []
        self.users: list[User] = []
        self.tariffs: list[Tariff] = []
        self.subscriptions: list[Subscription] = []
        self.flushed = 0
        self.committed = 0
        self.scalar_statements: list[Any] = []
        self._next_invoice_id = 100
        self._next_subscription_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, Invoice) and instance not in self.invoices:
            self.invoices.append(instance)
        if isinstance(instance, Subscription) and instance not in self.subscriptions:
            self.subscriptions.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for invoice in self.invoices:
            if invoice.id is None:
                invoice.id = self._next_invoice_id
                self._next_invoice_id += 1
        for subscription in self.subscriptions:
            if subscription.id is None:
                subscription.id = self._next_subscription_id
                self._next_subscription_id += 1

    async def commit(self) -> None:
        self.committed += 1

    async def scalar(self, statement: Any) -> Any:
        self.scalar_statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params

        if entity is Invoice:
            charge_id = _param_with_prefix_or_none(params, "telegram_payment_charge_id")
            if charge_id is not None:
                return next(
                    (
                        invoice
                        for invoice in self.invoices
                        if invoice.telegram_payment_charge_id == charge_id
                    ),
                    None,
                )
            payload = _param_with_prefix_or_none(params, "payload")
            if payload is not None:
                provider = _param_with_prefix_or_none(params, "provider")
                return next(
                    (
                        invoice
                        for invoice in self.invoices
                        if invoice.payload == payload
                        and (provider is None or invoice.provider == provider)
                    ),
                    None,
                )
            provider_invoice_id = _param_with_prefix(params, "provider_invoice_id")
            return next(
                (
                    invoice
                    for invoice in self.invoices
                    if invoice.provider is InvoiceProvider.cryptobot
                    and invoice.provider_invoice_id == provider_invoice_id
                ),
                None,
            )

        if entity is User:
            user_id = _param_with_prefix(params, "id")
            return next((user for user in self.users if user.id == user_id), None)

        if entity is Tariff:
            tariff_id = _param_with_prefix(params, "id")
            return next((tariff for tariff in self.tariffs if tariff.id == tariff_id), None)

        if entity is Subscription:
            user_id = _param_with_prefix(params, "user_id")
            return next(
                (
                    subscription
                    for subscription in self.subscriptions
                    if subscription.user_id == user_id
                ),
                None,
            )

        msg = f"unsupported fake scalar query for {entity}"
        raise AssertionError(msg)


def _param_with_prefix(params: dict[str, Any], prefix: str) -> Any:
    return next(value for key, value in params.items() if key.startswith(prefix))


def _param_with_prefix_or_none(params: dict[str, Any], prefix: str) -> Any:
    return next((value for key, value in params.items() if key.startswith(prefix)), None)


def make_user(*, user_id: int = 10, status: UserStatus = UserStatus.basic) -> User:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return User(
        id=user_id,
        tg_id=1001,
        username=None,
        first_name=None,
        lang_code="en",
        status=status,
        total_active_days=0,
        referrer_id=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_tariff(*, tariff_id: int = 20, duration_months: int = 1) -> Tariff:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return Tariff(
        id=tariff_id,
        code=f"{duration_months}m",
        duration_months=duration_months,
        base_price_usd=Decimal("79.00"),
        active=True,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_subscription(
    *,
    subscription_id: int = 1,
    user_id: int = 10,
    expires_at: datetime | None = None,
    last_invoice_id: int | None = 40,
) -> Subscription:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return Subscription(
        id=subscription_id,
        user_id=user_id,
        started_at=timestamp,
        expires_at=expires_at or timestamp + timedelta(days=30),
        last_invoice_id=last_invoice_id,
        stars_subscription_active=True,
        source=SubscriptionSource.stars,
        notified_3d_at=None,
        notified_0d_at=None,
        notified_post_at=None,
        access_revoked_at=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_invoice(
    *,
    invoice_id: int = 30,
    user_id: int = 10,
    tariff_id: int | None = 20,
    status: InvoiceStatus = InvoiceStatus.active,
    payload: str = "inv:payload",
    promo_extra_days: int = 0,
) -> Invoice:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return Invoice(
        id=invoice_id,
        user_id=user_id,
        provider=InvoiceProvider.cryptobot,
        provider_invoice_id="provider-1",
        telegram_payment_charge_id=None,
        tariff_id=tariff_id,
        amount_usd=Decimal("73.30"),
        gross=Decimal("73.30"),
        fee=Decimal("2.20"),
        net=Decimal("71.10"),
        currency="USD",
        status=status,
        payload=payload,
        provider_payload={"created": True},
        expires_at=timestamp + timedelta(hours=1),
        created_at=timestamp,
        paid_at=None,
        promo_id=5 if promo_extra_days else None,
        promo_code="BONUS7" if promo_extra_days else None,
        promo_kind=PromoKind.extra_days if promo_extra_days else None,
        promo_value=Decimal(str(promo_extra_days)) if promo_extra_days else None,
        promo_discount_usd=Decimal("0.00"),
        promo_extra_days=promo_extra_days,
    )


def make_stars_invoice(
    *,
    invoice_id: int = 40,
    user_id: int = 10,
    tariff_id: int | None = 20,
    status: InvoiceStatus = InvoiceStatus.active,
    payload: str = "stars:payload",
    stars_amount: int = 8682,
    charge_id: str | None = None,
    promo_extra_days: int = 0,
    expires_at: datetime | None = None,
) -> Invoice:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    provider_payload: dict[str, Any] = {
        "payment_currency": "XTR",
        "stars_amount": stars_amount,
        "subscription_period": 2_592_000,
    }
    if status is InvoiceStatus.paid:
        provider_payload["successful_payment"] = {
            "currency": "XTR",
            "total_amount": stars_amount,
            "invoice_payload": payload,
        }
    return Invoice(
        id=invoice_id,
        user_id=user_id,
        provider=InvoiceProvider.stars,
        provider_invoice_id=None,
        telegram_payment_charge_id=charge_id,
        tariff_id=tariff_id,
        amount_usd=Decimal("112.87"),
        gross=Decimal("112.87"),
        fee=Decimal("33.86"),
        net=Decimal("79.01"),
        currency="USD",
        status=status,
        payload=payload,
        provider_payload=provider_payload,
        expires_at=expires_at or timestamp + timedelta(hours=1),
        created_at=timestamp,
        paid_at=timestamp if status is InvoiceStatus.paid else None,
        promo_id=5 if promo_extra_days else None,
        promo_code="BONUS7" if promo_extra_days else None,
        promo_kind=PromoKind.extra_days if promo_extra_days else None,
        promo_value=Decimal(str(promo_extra_days)) if promo_extra_days else None,
        promo_discount_usd=Decimal("0.00"),
        promo_extra_days=promo_extra_days,
    )


def make_stars_payment(
    *,
    payload: str = "stars:payload",
    currency: str = "XTR",
    total_amount: int = 8682,
    charge_id: str = "charge-1",
    is_recurring: bool | None = None,
    is_first_recurring: bool | None = None,
) -> StarsSuccessfulPayment:
    raw = {"currency": currency, "total_amount": total_amount, "invoice_payload": payload}
    if is_recurring is not None:
        raw["is_recurring"] = is_recurring
    if is_first_recurring is not None:
        raw["is_first_recurring"] = is_first_recurring
    return StarsSuccessfulPayment(
        invoice_payload=payload,
        currency=currency,
        total_amount=total_amount,
        telegram_payment_charge_id=charge_id,
        is_recurring=is_recurring,
        is_first_recurring=is_first_recurring,
        subscription_expiration_date=None,
        raw=raw,
    )


def make_provider_invoice(
    *,
    invoice_id: str = "provider-1",
    status: str = "paid",
    payload: str | None = "inv:payload",
    raw: dict[str, Any] | None = None,
) -> CryptoBotInvoice:
    return CryptoBotInvoice(
        invoice_id=invoice_id,
        status=status,
        payload=payload,
        pay_url=None,
        raw=raw or {"invoice_id": invoice_id, "status": status, "payload": payload},
    )


async def test_process_paid_invoice_extends_subscription_once() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    tariff = make_tariff(duration_months=1)
    invoice = make_invoice()
    session.users.append(user)
    session.tariffs.append(tariff)
    session.invoices.append(invoice)

    result = await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(raw={"invoice_id": "provider-1"}),
        now=now,
    )

    assert result.processed is True
    assert result.invoice is invoice
    assert result.subscription is session.subscriptions[0]
    assert result.invoice_id == invoice.id
    assert result.provider_invoice_id == "provider-1"
    assert result.user_tg_id == 1001
    assert result.lang_code == "en"
    assert result.tariff_code == "1m"
    assert result.gross == Decimal("73.30")
    assert result.fee == Decimal("2.20")
    assert result.net == Decimal("71.10")
    assert result.currency == "USD"
    subscription = result.subscription
    assert subscription.expires_at == now + timedelta(days=30)
    assert result.subscription_expires_at == subscription.expires_at
    assert subscription.started_at == now
    assert subscription.source is SubscriptionSource.cryptobot
    assert subscription.last_invoice_id == invoice.id
    assert user.total_active_days == 30
    assert user.status is UserStatus.bronze
    assert invoice.status is InvoiceStatus.paid
    assert invoice.paid_at == now
    assert invoice.provider_payload == {"invoice_id": "provider-1"}
    assert invoice.gross == Decimal("73.30")
    assert invoice.fee == Decimal("2.20")
    assert invoice.net == Decimal("71.10")
    assert session.committed == 0


async def test_validate_stars_pre_checkout_accepts_active_matching_invoice() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_stars_invoice()
    session.invoices.append(invoice)

    result = await validate_stars_pre_checkout(
        session,
        payload="stars:payload",
        currency="XTR",
        total_amount=8682,
        now=now,
    )

    assert result.ok is True
    assert result.invoice is invoice
    assert result.error_message is None


async def test_validate_stars_pre_checkout_rejects_amount_mismatch() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    session.invoices.append(make_stars_invoice())

    result = await validate_stars_pre_checkout(
        session,
        payload="stars:payload",
        currency="XTR",
        total_amount=1,
        now=now,
    )

    assert result.ok is False
    assert result.error_message == "invoice amount mismatch"


async def test_process_paid_stars_invoice_extends_subscription_once() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    tariff = make_tariff(duration_months=1)
    invoice = make_stars_invoice()
    session.users.append(user)
    session.tariffs.append(tariff)
    session.invoices.append(invoice)

    result = await process_paid_stars_invoice(
        session,
        successful_payment=make_stars_payment(),
        now=now,
    )

    assert result.processed is True
    assert result.invoice is invoice
    assert result.subscription is session.subscriptions[0]
    assert result.invoice_id == invoice.id
    assert result.user_tg_id == 1001
    assert result.tariff_code == "1m"
    assert result.currency == "USD"
    assert result.gross == Decimal("112.87")
    assert result.fee == Decimal("33.86")
    assert result.net == Decimal("79.01")
    subscription = result.subscription
    assert subscription.expires_at == now + timedelta(days=30)
    assert subscription.source is SubscriptionSource.stars
    assert subscription.last_invoice_id == invoice.id
    assert subscription.stars_subscription_active is True
    assert user.total_active_days == 30
    assert invoice.status is InvoiceStatus.paid
    assert invoice.telegram_payment_charge_id == "charge-1"
    assert invoice.provider_payload == {
        "payment_currency": "XTR",
        "stars_amount": 8682,
        "subscription_period": 2_592_000,
        "successful_payment": {
            "currency": "XTR",
            "total_amount": 8682,
            "invoice_payload": "stars:payload",
        },
    }


async def test_process_paid_stars_invoice_accepts_success_after_local_ttl_expires() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    tariff = make_tariff(duration_months=1)
    invoice = make_stars_invoice(expires_at=now - timedelta(seconds=1))
    session.users.append(user)
    session.tariffs.append(tariff)
    session.invoices.append(invoice)

    result = await process_paid_stars_invoice(
        session,
        successful_payment=make_stars_payment(),
        now=now,
    )

    assert result.processed is True
    assert invoice.status is InvoiceStatus.paid
    assert result.subscription is session.subscriptions[0]


async def test_process_paid_stars_invoice_processes_recurring_charge_once_per_charge_id() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    tariff = make_tariff(duration_months=1)
    parent_invoice = make_stars_invoice(status=InvoiceStatus.paid, charge_id="charge-1")
    parent_invoice.provider_payload["access_delivered_at"] = "2026-06-17T12:00:00+00:00"
    subscription = make_subscription(expires_at=now + timedelta(days=10))
    session.users.append(user)
    session.tariffs.append(tariff)
    session.invoices.append(parent_invoice)
    session.subscriptions.append(subscription)

    result = await process_paid_stars_invoice(
        session,
        successful_payment=make_stars_payment(
            charge_id="charge-2",
            is_recurring=True,
            is_first_recurring=False,
        ),
        now=now,
    )

    renewal_invoice = result.invoice
    assert result.processed is True
    assert renewal_invoice is not parent_invoice
    assert renewal_invoice.id == 100
    assert renewal_invoice.status is InvoiceStatus.paid
    assert renewal_invoice.payload.startswith("stars:renew:")
    assert renewal_invoice.telegram_payment_charge_id == "charge-2"
    assert renewal_invoice.promo_id is None
    assert renewal_invoice.promo_extra_days == 0
    assert "access_delivered_at" not in renewal_invoice.provider_payload
    assert renewal_invoice.provider_payload["parent_invoice_id"] == parent_invoice.id
    assert renewal_invoice.provider_payload["successful_payment"]["is_recurring"] is True
    assert subscription.expires_at == now + timedelta(days=40)
    assert subscription.last_invoice_id == renewal_invoice.id
    assert user.total_active_days == 30


async def test_process_paid_stars_invoice_is_idempotent_by_charge_id() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_stars_invoice(status=InvoiceStatus.paid, charge_id="charge-1")
    subscription = make_subscription()
    session.users.append(make_user())
    session.tariffs.append(make_tariff())
    session.invoices.append(invoice)
    session.subscriptions.append(subscription)

    result = await process_paid_stars_invoice(
        session,
        successful_payment=make_stars_payment(),
        now=now,
    )

    assert result.processed is False
    assert result.invoice is invoice
    assert result.subscription is subscription
    assert result.user_tg_id == 1001
    assert result.lang_code == "en"
    assert result.tariff_code == "1m"


async def test_process_paid_invoice_applies_extra_days_without_loyalty_credit() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    tariff = make_tariff(duration_months=1)
    invoice = make_invoice(promo_extra_days=7)
    session.users.append(user)
    session.tariffs.append(tariff)
    session.invoices.append(invoice)

    result = await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(raw={"invoice_id": "provider-1"}),
        now=now,
    )

    assert result.processed is True
    assert result.subscription is not None
    assert result.subscription.expires_at == now + timedelta(days=37)
    assert user.total_active_days == 30


async def test_process_paid_invoice_rejects_non_paid_provider_status() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice()
    session.users.append(make_user())
    session.tariffs.append(make_tariff())
    session.invoices.append(invoice)

    with pytest.raises(ValueError, match="provider invoice is not paid"):
        await process_paid_cryptobot_invoice(
            session,
            provider_invoice=make_provider_invoice(status="active"),
            now=now,
        )

    assert invoice.status is InvoiceStatus.active
    assert session.subscriptions == []
    assert session.flushed == 0


async def test_process_paid_invoice_preserves_usd_quote_for_unconverted_crypto_amounts() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice()
    session.users.append(make_user())
    session.tariffs.append(make_tariff())
    session.invoices.append(invoice)

    result = await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(
            raw={
                "invoice_id": "provider-1",
                "status": "paid",
                "payload": "inv:payload",
                "currency_type": "fiat",
                "fiat": "USD",
                "paid_asset": "BTC",
                "paid_amount": "0.001128",
                "fee_asset": "BTC",
                "fee_amount": "0.000033",
            },
        ),
        now=now,
    )

    assert invoice.gross == Decimal("73.30")
    assert invoice.amount_usd == Decimal("73.30")
    assert invoice.fee == Decimal("2.20")
    assert invoice.net == Decimal("71.10")
    assert result.gross == Decimal("73.30")
    assert result.fee == Decimal("2.20")
    assert result.net == Decimal("71.10")


async def test_process_paid_invoice_converts_provider_fee_when_usd_rate_is_available() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice()
    session.users.append(make_user())
    session.tariffs.append(make_tariff())
    session.invoices.append(invoice)

    result = await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(
            raw={
                "invoice_id": "provider-1",
                "status": "paid",
                "payload": "inv:payload",
                "currency_type": "fiat",
                "fiat": "USD",
                "amount": "73.30",
                "paid_asset": "BTC",
                "paid_amount": "0.001128",
                "paid_usd_rate": "65000",
                "fee_asset": "BTC",
                "fee_amount": "0.000033",
            },
        ),
        now=now,
    )

    assert invoice.gross == Decimal("73.30")
    assert invoice.amount_usd == Decimal("73.30")
    assert invoice.fee == Decimal("2.145000")
    assert invoice.net == Decimal("71.155000")
    assert result.gross == Decimal("73.30")
    assert result.fee == Decimal("2.145000")
    assert result.net == Decimal("71.155000")


async def test_process_paid_invoice_is_idempotent_for_already_paid_invoice() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    session.users.append(user)
    session.tariffs.append(make_tariff())
    session.invoices.append(make_invoice())

    first = await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(),
        now=now,
    )
    first_expiry = first.subscription.expires_at
    first_active_days = user.total_active_days
    first_flushes = session.flushed

    second = await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(),
        now=now + timedelta(minutes=1),
    )

    assert second.processed is False
    assert second.invoice is first.invoice
    assert second.subscription is None
    assert first.subscription.expires_at == first_expiry
    assert user.total_active_days == first_active_days
    assert session.flushed == first_flushes


async def test_process_paid_invoice_rejects_payload_mismatch_before_mutation() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice(payload="inv:local")
    session.users.append(make_user())
    session.tariffs.append(make_tariff())
    session.invoices.append(invoice)

    with pytest.raises(ValueError, match="payload mismatch"):
        await process_paid_cryptobot_invoice(
            session,
            provider_invoice=make_provider_invoice(payload="inv:provider"),
            now=now,
        )

    assert invoice.status is InvoiceStatus.active
    assert session.subscriptions == []
    assert session.flushed == 0


async def test_process_paid_invoice_raises_when_local_invoice_missing() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    with pytest.raises(LookupError, match="invoice.*not found"):
        await process_paid_cryptobot_invoice(
            session,
            provider_invoice=make_provider_invoice(),
            now=now,
        )


async def test_process_paid_invoice_rejects_missing_tariff_id() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice(tariff_id=None)
    session.users.append(make_user())
    session.invoices.append(invoice)

    with pytest.raises(ValueError, match="tariff_id"):
        await process_paid_cryptobot_invoice(
            session,
            provider_invoice=make_provider_invoice(),
            now=now,
        )

    assert invoice.status is InvoiceStatus.active
    assert session.subscriptions == []
    assert session.flushed == 0


async def test_process_paid_invoice_raises_when_tariff_row_missing() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice(tariff_id=999)
    session.users.append(make_user())
    session.invoices.append(invoice)

    with pytest.raises(LookupError, match="tariff.*not found"):
        await process_paid_cryptobot_invoice(
            session,
            provider_invoice=make_provider_invoice(),
            now=now,
        )

    assert invoice.status is InvoiceStatus.active
    assert session.subscriptions == []
    assert session.flushed == 0


async def test_process_paid_invoice_raises_when_user_missing() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice(user_id=404)
    session.tariffs.append(make_tariff())
    session.invoices.append(invoice)

    with pytest.raises(LookupError, match="user.*not found"):
        await process_paid_cryptobot_invoice(
            session,
            provider_invoice=make_provider_invoice(),
            now=now,
        )

    assert invoice.status is InvoiceStatus.active
    assert session.subscriptions == []
    assert session.flushed == 0
