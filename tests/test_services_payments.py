from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from bot.config import Settings
from bot.models import (
    Invoice,
    InvoiceProvider,
    InvoiceStatus,
    PromoKind,
    Referral,
    ReferralEarning,
    Subscription,
    SubscriptionSource,
    Tariff,
    User,
    UserStatus,
)
from bot.payments.cryptobot import CryptoBotInvoice
from bot.payments.xrocket import XRocketAPIError, XRocketInvoice
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
        self.referrals: list[Referral] = []
        self.earnings: list[ReferralEarning] = []
        self.flushed = 0
        self.committed = 0
        self.executed_statements: list[Any] = []
        self.scalar_statements: list[Any] = []
        self._next_invoice_id = 100
        self._next_subscription_id = 1
        self._next_earning_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, Invoice) and instance not in self.invoices:
            self.invoices.append(instance)
        if isinstance(instance, Subscription) and instance not in self.subscriptions:
            self.subscriptions.append(instance)
        if isinstance(instance, Referral) and instance not in self.referrals:
            self.referrals.append(instance)
        if isinstance(instance, ReferralEarning) and instance not in self.earnings:
            self.earnings.append(instance)

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
        for earning in self.earnings:
            if earning.id is None:
                earning.id = self._next_earning_id
                self._next_earning_id += 1

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        pass

    async def __aenter__(self) -> FakeAsyncSession:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def execute(self, statement: Any) -> None:
        self.executed_statements.append(statement)
        if getattr(getattr(statement, "table", None), "name", None) != "referral_earnings":
            msg = f"unsupported fake execute for {statement}"
            raise AssertionError(msg)

        params = statement.compile(dialect=postgresql.dialect()).params
        if any(earning.invoice_id == params["invoice_id"] for earning in self.earnings):
            return
        self.earnings.append(
            ReferralEarning(
                id=self._next_earning_id,
                referrer_id=params["referrer_id"],
                referee_id=params["referee_id"],
                invoice_id=params["invoice_id"],
                amount_usd=params["amount_usd"],
                created_at=params["created_at"],
                paid_out=params["paid_out"],
                paid_out_at=params["paid_out_at"],
            )
        )
        self._next_earning_id += 1

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
            provider = _param_with_prefix_or_none(params, "provider")
            return next(
                (
                    invoice
                    for invoice in self.invoices
                    if (provider is None or invoice.provider == provider)
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

        if entity is Referral:
            referee_id = _param_with_prefix_or_none(params, "referee_id")
            return next(
                (
                    referral
                    for referral in self.referrals
                    if referee_id is None or referral.referee_id == referee_id
                ),
                None,
            )

        if entity is ReferralEarning:
            invoice_id = _param_with_prefix_or_none(params, "invoice_id")
            return next(
                (
                    earning
                    for earning in self.earnings
                    if invoice_id is None or earning.invoice_id == invoice_id
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
        referral_percent=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_referral(
    *,
    referral_id: int = 1,
    referrer_id: int = 99,
    referee_id: int = 10,
    custom_percent: Decimal | None = None,
) -> Referral:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return Referral(
        id=referral_id,
        referrer_id=referrer_id,
        referee_id=referee_id,
        joined_at=timestamp,
        custom_percent=custom_percent,
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
    provider: InvoiceProvider = InvoiceProvider.cryptobot,
    provider_invoice_id: str = "provider-1",
    tariff_id: int | None = 20,
    status: InvoiceStatus = InvoiceStatus.active,
    payload: str = "inv:payload",
    promo_extra_days: int = 0,
    currency: str = "USD",
) -> Invoice:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return Invoice(
        id=invoice_id,
        user_id=user_id,
        provider=provider,
        provider_invoice_id=provider_invoice_id,
        telegram_payment_charge_id=None,
        tariff_id=tariff_id,
        amount_usd=Decimal("73.30"),
        gross=Decimal("73.30"),
        fee=Decimal("2.20"),
        net=Decimal("71.10"),
        currency=currency,
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


def make_xrocket_invoice(
    *,
    invoice_id: str = "provider-1",
    status: str = "paid",
    payload: str | None = "inv:payload",
    pay_url: str | None = "https://t.me/xrocket?start=pay",
    paid_at: datetime | None = None,
    raw: dict[str, Any] | None = None,
) -> XRocketInvoice:
    return XRocketInvoice(
        invoice_id=invoice_id,
        status=status,
        amount=Decimal("73.30"),
        currency="USDT",
        payload=payload,
        pay_url=pay_url,
        created_at=datetime(2026, 6, 17, 12, 0, tzinfo=UTC),
        paid_at=paid_at,
        raw=raw or {"id": invoice_id, "status": status, "payload": payload},
    )


async def test_create_xrocket_invoice_for_user_commits_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bot.services.payments as payments_module

    settings = Settings(xrocket={"enabled": True, "fee_percent": Decimal("5")})
    user = make_user(user_id=7)
    session = FakeAsyncSession()
    session.users.append(user)
    provider_invoice = make_xrocket_invoice(
        invoice_id="123",
        status="active",
        payload="inv:00000000-0000-0000-0000-000000000001",
        raw={"id": 123, "link": "https://t.me/xrocket?start=pay"},
    )
    client_calls: list[dict[str, Any]] = []
    repo_calls: list[dict[str, Any]] = []

    class FakeSessionFactory:
        def __call__(self) -> FakeAsyncSession:
            return session

    class FakeClient:
        async def create_invoice(self, **kwargs: Any) -> XRocketInvoice:
            client_calls.append({"commits_before_provider": session.committed})
            client_calls.append(kwargs)
            return provider_invoice

    async def fake_get_pending_promo_for_user(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[User, None]:
        return user, None

    async def fake_ensure_configured_tariff(*args: Any, **kwargs: Any) -> Tariff:
        repo_calls.append({"name": "tariff", "args": args, **kwargs})
        return make_tariff(tariff_id=55, duration_months=1)

    async def fake_create_pending_provider_invoice(*args: Any, **kwargs: Any) -> Invoice:
        repo_calls.append({"name": "pending", "args": args, **kwargs})
        invoice = make_invoice(
            invoice_id=77,
            user_id=user.id,
            provider=InvoiceProvider.xrocket,
            provider_invoice_id=None,
            payload=kwargs["payload"],
            currency=kwargs["currency"],
        )
        session.invoices.append(invoice)
        return invoice

    async def fake_lock_invoice_by_id(*args: Any, **kwargs: Any) -> Invoice | None:
        repo_calls.append({"name": "lock", "args": args, **kwargs})
        return session.invoices[0]

    async def fake_mark_provider_created(*args: Any, **kwargs: Any) -> Invoice:
        repo_calls.append({"name": "mark", "args": args, **kwargs})
        invoice = args[1]
        invoice.provider_invoice_id = kwargs["provider_invoice_id"]
        invoice.provider_payload = kwargs["raw_payload"]
        return invoice

    monkeypatch.setattr(payments_module.uuid, "uuid4", lambda: UUID(int=1))
    monkeypatch.setattr(
        payments_module,
        "get_pending_promo_for_user",
        fake_get_pending_promo_for_user,
    )
    monkeypatch.setattr(
        payments_module,
        "ensure_configured_tariff",
        fake_ensure_configured_tariff,
    )
    monkeypatch.setattr(
        payments_module,
        "create_pending_provider_invoice",
        fake_create_pending_provider_invoice,
    )
    monkeypatch.setattr(payments_module, "lock_invoice_by_id", fake_lock_invoice_by_id)
    monkeypatch.setattr(
        payments_module,
        "mark_invoice_provider_created",
        fake_mark_provider_created,
    )

    created = await payments_module.create_xrocket_invoice_for_user(
        FakeSessionFactory(),
        settings,
        FakeClient(),
        user,
        "1m",
        datetime(2026, 6, 17, 12, 0, tzinfo=UTC),
    )

    assert repo_calls[0]["name"] == "tariff"
    assert repo_calls[1]["name"] == "pending"
    assert repo_calls[1]["provider"] is InvoiceProvider.xrocket
    assert repo_calls[1]["currency"] == "USDT"
    assert repo_calls[1]["expires_at"] == datetime(2026, 6, 17, 13, 0, tzinfo=UTC)
    assert client_calls == [
        {"commits_before_provider": 1},
        {
            "amount": Decimal("62.11"),
            "payload": "inv:00000000-0000-0000-0000-000000000001",
            "description": "Hydra Monitors access: 1 month",
            "currency": "USDT",
            "expires_in": 3600,
        },
    ]
    assert repo_calls[2]["name"] == "lock"
    assert repo_calls[2]["args"] == (session, 77)
    assert repo_calls[3]["name"] == "mark"
    assert repo_calls[3]["provider_invoice_id"] == "123"
    assert repo_calls[3]["raw_payload"] == provider_invoice.raw
    assert created.invoice.provider_invoice_id == "123"
    assert created.pay_url == "https://t.me/xrocket?start=pay"
    assert created.quote.gross_usd == Decimal("62.11")
    assert session.committed == 2


async def test_create_xrocket_invoice_for_user_cancels_local_invoice_on_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bot.services.payments as payments_module

    user = make_user(user_id=7)
    session = FakeAsyncSession()
    cancelled: list[int] = []

    class FakeSessionFactory:
        def __call__(self) -> FakeAsyncSession:
            return session

    class FakeClient:
        async def create_invoice(self, **kwargs: Any) -> XRocketInvoice:
            del kwargs
            raise XRocketAPIError("TOKEN_INVALID")

    async def fake_get_pending_promo_for_user(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[User, None]:
        return user, None

    async def fake_ensure_configured_tariff(*args: Any, **kwargs: Any) -> Tariff:
        return make_tariff(tariff_id=55)

    async def fake_create_pending_provider_invoice(*args: Any, **kwargs: Any) -> Invoice:
        del args, kwargs
        invoice = make_invoice(
            invoice_id=77,
            user_id=user.id,
            provider=InvoiceProvider.xrocket,
            provider_invoice_id=None,
            currency="USDT",
        )
        return invoice

    async def fake_cancel_unusable_invoice(*args: Any, **kwargs: Any) -> None:
        cancelled.append(args[1])

    monkeypatch.setattr(
        payments_module,
        "get_pending_promo_for_user",
        fake_get_pending_promo_for_user,
    )
    monkeypatch.setattr(
        payments_module,
        "ensure_configured_tariff",
        fake_ensure_configured_tariff,
    )
    monkeypatch.setattr(
        payments_module,
        "create_pending_provider_invoice",
        fake_create_pending_provider_invoice,
    )
    monkeypatch.setattr(payments_module, "cancel_unusable_invoice", fake_cancel_unusable_invoice)

    with pytest.raises(XRocketAPIError, match="TOKEN_INVALID"):
        await payments_module.create_xrocket_invoice_for_user(
            FakeSessionFactory(),
            Settings(xrocket={"enabled": True}),
            FakeClient(),
            user,
            "1m",
            datetime(2026, 6, 17, 12, 0, tzinfo=UTC),
        )

    assert cancelled == [77]


async def test_create_xrocket_invoice_for_user_rejects_missing_provider_pay_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bot.services.payments as payments_module

    user = make_user(user_id=7)
    session = FakeAsyncSession()
    mark_calls: list[object] = []

    class FakeSessionFactory:
        def __call__(self) -> FakeAsyncSession:
            return session

    class FakeClient:
        async def create_invoice(self, **kwargs: Any) -> XRocketInvoice:
            del kwargs
            return make_xrocket_invoice(
                invoice_id="123",
                status="active",
                pay_url="   ",
                raw={"id": 123},
            )

    async def fake_get_pending_promo_for_user(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[User, None]:
        return user, None

    async def fake_ensure_configured_tariff(*args: Any, **kwargs: Any) -> Tariff:
        return make_tariff(tariff_id=55)

    async def fake_create_pending_provider_invoice(*args: Any, **kwargs: Any) -> Invoice:
        del args, kwargs
        return make_invoice(
            invoice_id=77,
            user_id=user.id,
            provider=InvoiceProvider.xrocket,
            provider_invoice_id=None,
            currency="USDT",
        )

    async def fake_mark_provider_created(*args: Any, **kwargs: Any) -> None:
        del kwargs
        mark_calls.append(args)

    async def fake_lock_invoice_by_id(*args: Any, **kwargs: Any) -> Invoice:
        del args, kwargs
        return make_invoice(
            invoice_id=77,
            user_id=user.id,
            provider=InvoiceProvider.xrocket,
            provider_invoice_id=None,
            currency="USDT",
        )

    monkeypatch.setattr(
        payments_module,
        "get_pending_promo_for_user",
        fake_get_pending_promo_for_user,
    )
    monkeypatch.setattr(
        payments_module,
        "ensure_configured_tariff",
        fake_ensure_configured_tariff,
    )
    monkeypatch.setattr(
        payments_module,
        "create_pending_provider_invoice",
        fake_create_pending_provider_invoice,
    )
    monkeypatch.setattr(
        payments_module,
        "mark_invoice_provider_created",
        fake_mark_provider_created,
    )
    monkeypatch.setattr(payments_module, "lock_invoice_by_id", fake_lock_invoice_by_id)

    with pytest.raises(XRocketAPIError, match="missing pay URL"):
        await payments_module.create_xrocket_invoice_for_user(
            FakeSessionFactory(),
            Settings(xrocket={"enabled": True}),
            FakeClient(),
            user,
            "1m",
            datetime(2026, 6, 17, 12, 0, tzinfo=UTC),
        )

    assert mark_calls == []


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


async def test_process_paid_xrocket_invoice_extends_subscription_once() -> None:
    from bot.services.payments import process_paid_xrocket_invoice

    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    tariff = make_tariff(duration_months=1)
    invoice = make_invoice(provider=InvoiceProvider.xrocket, currency="USDT")
    session.users.append(user)
    session.tariffs.append(tariff)
    session.invoices.append(invoice)

    result = await process_paid_xrocket_invoice(
        session,
        provider_invoice=make_xrocket_invoice(raw={"id": "provider-1"}),
        now=now,
    )

    assert result.processed is True
    assert result.invoice is invoice
    assert result.subscription is session.subscriptions[0]
    assert result.provider_invoice_id == "provider-1"
    assert result.currency == "USDT"
    assert result.gross == Decimal("73.30")
    assert result.fee == Decimal("2.20")
    assert result.net == Decimal("71.10")
    subscription = result.subscription
    assert subscription.expires_at == now + timedelta(days=30)
    assert subscription.source is SubscriptionSource.xrocket
    assert subscription.last_invoice_id == invoice.id
    assert invoice.status is InvoiceStatus.paid
    assert invoice.paid_at == now
    assert invoice.provider_payload == {"id": "provider-1"}


async def test_process_paid_invoice_records_referral_earning_from_net_amount() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    referrer = make_user(user_id=99)
    tariff = make_tariff(duration_months=1)
    invoice = make_invoice()
    session.users.extend([user, referrer])
    session.tariffs.append(tariff)
    session.invoices.append(invoice)
    session.referrals.append(make_referral(referrer_id=99, referee_id=user.id))

    result = await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(raw={"invoice_id": "provider-1"}),
        now=now,
    )

    assert result.processed is True
    assert len(session.earnings) == 1
    earning = session.earnings[0]
    assert earning.referrer_id == 99
    assert earning.referee_id == user.id
    assert earning.invoice_id == invoice.id
    assert earning.amount_usd == Decimal("7.11")


async def test_process_paid_invoice_uses_custom_referral_percent() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    referrer = make_user(user_id=99)
    invoice = make_invoice()
    session.users.extend([user, referrer])
    session.tariffs.append(make_tariff(duration_months=1))
    session.invoices.append(invoice)
    session.referrals.append(
        make_referral(referrer_id=99, referee_id=user.id, custom_percent=Decimal("15")),
    )

    await process_paid_cryptobot_invoice(
        session,
        provider_invoice=make_provider_invoice(raw={"invoice_id": "provider-1"}),
        now=now,
    )

    assert session.earnings[0].amount_usd == Decimal("10.67")


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


async def test_process_paid_stars_recurring_invoice_rechecks_duplicate_charge_after_parent_lock(
    monkeypatch,
) -> None:
    import bot.services.payments as payments_module

    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    tariff = make_tariff(duration_months=1)
    parent_invoice = make_stars_invoice(status=InvoiceStatus.paid, charge_id="charge-1")
    renewal_invoice = make_stars_invoice(
        status=InvoiceStatus.paid,
        charge_id="charge-2",
        payload="stars:renew:existing",
    )
    renewal_invoice.id = 200
    subscription = make_subscription(expires_at=now + timedelta(days=10))
    session.users.append(user)
    session.tariffs.append(tariff)
    session.invoices.extend([parent_invoice, renewal_invoice])
    session.subscriptions.append(subscription)
    charge_lookups = 0

    async def fake_lock_by_charge_id(*args: Any, **kwargs: Any) -> Invoice | None:
        nonlocal charge_lookups
        charge_lookups += 1
        return renewal_invoice if charge_lookups == 2 else None

    async def fail_create_recurring_invoice(*args: Any, **kwargs: Any) -> Invoice:
        raise AssertionError("duplicate recurring charge must not create another invoice")

    monkeypatch.setattr(
        payments_module,
        "lock_invoice_by_telegram_payment_charge_id",
        fake_lock_by_charge_id,
    )
    monkeypatch.setattr(
        payments_module,
        "create_paid_stars_recurring_invoice",
        fail_create_recurring_invoice,
    )

    result = await process_paid_stars_invoice(
        session,
        successful_payment=make_stars_payment(
            charge_id="charge-2",
            is_recurring=True,
            is_first_recurring=False,
        ),
        now=now,
    )

    assert charge_lookups == 2
    assert result.processed is False
    assert result.invoice is renewal_invoice
    assert subscription.expires_at == now + timedelta(days=10)


async def test_process_paid_stars_recurring_invoice_records_referral_earning() -> None:
    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    referrer = make_user(user_id=99)
    tariff = make_tariff(duration_months=1)
    parent_invoice = make_stars_invoice(status=InvoiceStatus.paid, charge_id="charge-1")
    subscription = make_subscription(expires_at=now + timedelta(days=10))
    session.users.extend([user, referrer])
    session.tariffs.append(tariff)
    session.invoices.append(parent_invoice)
    session.subscriptions.append(subscription)
    session.referrals.append(make_referral(referrer_id=99, referee_id=user.id))

    result = await process_paid_stars_invoice(
        session,
        successful_payment=make_stars_payment(
            charge_id="charge-2",
            is_recurring=True,
            is_first_recurring=False,
        ),
        now=now,
    )

    assert result.processed is True
    assert len(session.earnings) == 1
    assert session.earnings[0].invoice_id == result.invoice_id
    assert session.earnings[0].amount_usd == Decimal("7.90")


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


async def test_process_paid_xrocket_invoice_is_idempotent_for_already_paid_invoice() -> None:
    from bot.services.payments import process_paid_xrocket_invoice

    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    user = make_user()
    session.users.append(user)
    session.tariffs.append(make_tariff())
    session.invoices.append(make_invoice(provider=InvoiceProvider.xrocket, currency="USDT"))

    first = await process_paid_xrocket_invoice(
        session,
        provider_invoice=make_xrocket_invoice(),
        now=now,
    )
    first_expiry = first.subscription.expires_at
    first_active_days = user.total_active_days
    first_flushes = session.flushed

    second = await process_paid_xrocket_invoice(
        session,
        provider_invoice=make_xrocket_invoice(),
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


async def test_process_paid_xrocket_invoice_rejects_payload_mismatch_before_mutation() -> None:
    from bot.services.payments import process_paid_xrocket_invoice

    session = FakeAsyncSession()
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    invoice = make_invoice(
        provider=InvoiceProvider.xrocket,
        payload="inv:local",
        currency="USDT",
    )
    session.users.append(make_user())
    session.tariffs.append(make_tariff())
    session.invoices.append(invoice)

    with pytest.raises(ValueError, match="payload mismatch"):
        await process_paid_xrocket_invoice(
            session,
            provider_invoice=make_xrocket_invoice(payload="inv:provider"),
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
