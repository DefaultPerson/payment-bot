from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.models import (
    Invoice,
    InvoiceProvider,
    InvoiceStatus,
    Subscription,
    SubscriptionSource,
    Tariff,
    User,
)
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotInvoice
from bot.payments.stars import STARS_CURRENCY, StarsInvoiceQuote, quote_stars_invoice
from bot.payments.tribute import TributeDigitalProductEvent
from bot.payments.xrocket import XRocketAPIError, XRocketInvoice
from bot.repos.invoices import (
    create_paid_stars_recurring_invoice,
    create_pending_cryptobot_invoice,
    create_pending_provider_invoice,
    create_pending_stars_invoice,
    lock_invoice_by_id,
    lock_invoice_by_payload,
    lock_invoice_by_provider_id,
    lock_invoice_by_telegram_payment_charge_id,
    lock_provider_invoice_by_provider_id,
    mark_invoice_paid,
    mark_invoice_provider_created,
    mark_stars_invoice_paid,
)
from bot.repos.promos import (
    get_pending_promo_for_user,
    promo_snapshot_from_row,
    release_promo_redemption_for_invoice,
    reserve_promo_redemption,
)
from bot.repos.referrals import get_referral_by_referee_id
from bot.repos.subscriptions import extend_paid_subscription
from bot.repos.tariffs import ensure_configured_tariff, resolve_plan_code
from bot.services.pricing import TariffQuote, quote_tariff, tariff_duration_days
from bot.services.referrals import record_referral_earning

logger = logging.getLogger(__name__)


class SupportsCreateCryptoBotInvoice(Protocol):
    async def create_invoice(
        self,
        *,
        amount: Decimal,
        payload: str,
        description: str,
        paid_btn_url: str,
        accepted_assets: list[str],
        expires_in: int,
    ) -> CryptoBotInvoice: ...


class SupportsCreateXRocketInvoice(Protocol):
    async def create_invoice(
        self,
        *,
        amount: Decimal,
        payload: str,
        description: str,
        currency: str,
        expires_in: int,
    ) -> XRocketInvoice: ...


@dataclass(frozen=True, slots=True)
class CreatedInvoice:
    invoice: Invoice
    quote: TariffQuote
    pay_url: str


@dataclass(frozen=True, slots=True)
class CreatedStarsInvoice:
    invoice: Invoice
    quote: TariffQuote
    stars: StarsInvoiceQuote
    payload: str
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class PreparedInvoice:
    invoice_id: int
    quote: TariffQuote
    payload: str
    description: str


@dataclass(frozen=True, slots=True)
class PaidInvoiceResult:
    processed: bool
    invoice: Invoice
    subscription: Subscription | None
    invoice_id: int | None = None
    provider_invoice_id: str | None = None
    user_tg_id: int | None = None
    username: str | None = None
    lang_code: str | None = None
    tariff_code: str | None = None
    gross: Decimal | None = None
    fee: Decimal | None = None
    net: Decimal | None = None
    currency: str | None = None
    subscription_expires_at: datetime | None = None
    plan_code: str | None = None


@dataclass(frozen=True, slots=True)
class StarsSuccessfulPayment:
    invoice_payload: str
    currency: str
    total_amount: int
    telegram_payment_charge_id: str
    raw: dict[str, Any]
    is_recurring: bool | None = None
    is_first_recurring: bool | None = None
    subscription_expiration_date: int | None = None


@dataclass(frozen=True, slots=True)
class StarsPreCheckoutResult:
    ok: bool
    invoice: Invoice | None = None
    error_message: str | None = None


async def create_cryptobot_invoice_for_user(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: SupportsCreateCryptoBotInvoice,
    user: User,
    tariff_code: str,
    paid_btn_url: str,
    now: datetime,
) -> CreatedInvoice:
    async with session_scope(session_factory) as session:
        prepared = await _prepare_local_invoice(
            session,
            settings=settings,
            user=user,
            tariff_code=tariff_code,
            now=now,
            provider=InvoiceProvider.cryptobot,
            currency="USD",
            expires_seconds=settings.cryptobot.invoice_expires_seconds,
            fee_percent=settings.cryptobot.fee_percent,
        )

    try:
        provider_invoice = await _create_provider_invoice(
            client,
            settings=settings,
            prepared=prepared,
            paid_btn_url=paid_btn_url,
        )
    except CryptoBotAPIError:
        await cancel_unusable_invoice(session_factory, prepared.invoice_id)
        raise

    async with session_scope(session_factory) as session:
        invoice = await lock_invoice_by_id(session, prepared.invoice_id)
        if invoice is None:
            msg = f"invoice {prepared.invoice_id} was not found"
            raise LookupError(msg)
        invoice = await mark_invoice_provider_created(
            session,
            invoice,
            provider_invoice_id=provider_invoice.invoice_id,
            raw_payload=provider_invoice.raw,
        )
    return CreatedInvoice(invoice=invoice, quote=prepared.quote, pay_url=provider_invoice.pay_url)


async def create_xrocket_invoice_for_user(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: SupportsCreateXRocketInvoice,
    user: User,
    tariff_code: str,
    now: datetime,
) -> CreatedInvoice:
    async with session_scope(session_factory) as session:
        prepared = await _prepare_local_invoice(
            session,
            settings=settings,
            user=user,
            tariff_code=tariff_code,
            now=now,
            provider=InvoiceProvider.xrocket,
            currency=settings.xrocket.currency,
            expires_seconds=settings.xrocket.invoice_expires_seconds,
            fee_percent=settings.xrocket.fee_percent,
        )

    try:
        provider_invoice = await _create_xrocket_provider_invoice(
            client,
            settings=settings,
            prepared=prepared,
        )
    except XRocketAPIError:
        await cancel_unusable_invoice(session_factory, prepared.invoice_id)
        raise

    async with session_scope(session_factory) as session:
        invoice = await lock_invoice_by_id(session, prepared.invoice_id)
        if invoice is None:
            msg = f"invoice {prepared.invoice_id} was not found"
            raise LookupError(msg)
        invoice = await mark_invoice_provider_created(
            session,
            invoice,
            provider_invoice_id=provider_invoice.invoice_id,
            raw_payload=provider_invoice.raw,
        )
    return CreatedInvoice(invoice=invoice, quote=prepared.quote, pay_url=provider_invoice.pay_url)


async def create_stars_invoice_for_user(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user: User,
    tariff_code: str,
    now: datetime,
) -> CreatedStarsInvoice:
    async with session_scope(session_factory) as session:
        locked_user, promo = await get_pending_promo_for_user(
            session,
            user=user,
            tariff_code=tariff_code,
            now=now,
            lock_user=True,
        )
        quote = quote_tariff(
            settings,
            tariff_code,
            locked_user.status,
            promo=promo_snapshot_from_row(promo) if promo is not None else None,
        )
        stars = quote_stars_invoice(settings, quote)
        stars_accounting_quote = replace(
            quote,
            gross_usd=stars.gross_usd,
            fee_usd=stars.fee_usd,
            net_usd=stars.net_usd,
        )
        tariff = await ensure_configured_tariff(session, settings, tariff_code)
        payload = f"stars:{uuid.uuid4()}"
        invoice = await create_pending_stars_invoice(
            session,
            user=locked_user,
            tariff_id=tariff.id,
            quote=stars_accounting_quote,
            stars_amount=stars.amount,
            subscription_period=stars.subscription_period,
            expires_at=now + timedelta(seconds=settings.stars.invoice_expires_seconds),
            payload=payload,
        )
        if invoice.id is None:
            msg = "invoice must have an id after flush"
            raise ValueError(msg)
        if promo is not None:
            await reserve_promo_redemption(
                session,
                promo=promo,
                user=locked_user,
                invoice=invoice,
                now=now,
            )
        return CreatedStarsInvoice(
            invoice=invoice,
            quote=quote,
            stars=stars,
            payload=payload,
            title="Hydra Monitors",
            description=_invoice_description(quote),
        )


async def process_paid_cryptobot_invoice(
    session: AsyncSession,
    *,
    provider_invoice: CryptoBotInvoice,
    now: datetime,
    settings: Settings | None = None,
) -> PaidInvoiceResult:
    invoice = await lock_invoice_by_provider_id(session, provider_invoice.invoice_id)
    if invoice is None:
        msg = f"invoice for provider invoice {provider_invoice.invoice_id} was not found"
        raise LookupError(msg)

    if invoice.status is InvoiceStatus.paid:
        return _paid_result(
            processed=False,
            invoice=invoice,
            subscription=None,
            user=None,
            tariff=None,
        )

    if provider_invoice.status != "paid":
        msg = "provider invoice is not paid"
        raise ValueError(msg)
    if provider_invoice.payload != invoice.payload:
        msg = "provider invoice payload mismatch"
        raise ValueError(msg)

    if invoice.tariff_id is None:
        msg = "invoice tariff_id is missing"
        raise ValueError(msg)
    if invoice.id is None:
        msg = "invoice must have an id before payment processing"
        raise ValueError(msg)

    user = await _load_paid_invoice_user(session, invoice.user_id)
    tariff = await _load_paid_invoice_tariff(session, invoice.tariff_id)
    subscription = await extend_paid_subscription(
        session,
        user=user,
        days=tariff_duration_days(tariff.duration_months),
        source=SubscriptionSource.cryptobot,
        last_invoice_id=invoice.id,
        now=now,
        bonus_days=invoice.promo_extra_days or 0,
        loyalty=settings.loyalty if settings is not None else None,
        plan_code=resolve_plan_code(settings, tariff.code) if settings is not None else None,
        settings=settings,
    )
    paid_amounts = _paid_amounts_from_payload(provider_invoice.raw)
    await mark_invoice_paid(
        session,
        invoice,
        paid_at=now,
        raw_payload=provider_invoice.raw,
        gross=paid_amounts.gross,
        fee=paid_amounts.fee,
        net=paid_amounts.net,
    )
    await _record_paid_referral_earning(
        session,
        invoice=invoice,
        user=user,
        now=now,
        settings=settings,
    )
    return _paid_result(
        processed=True,
        invoice=invoice,
        subscription=subscription,
        user=user,
        tariff=tariff,
    )


async def process_paid_xrocket_invoice(
    session: AsyncSession,
    *,
    provider_invoice: XRocketInvoice,
    now: datetime,
    settings: Settings | None = None,
) -> PaidInvoiceResult:
    invoice = await lock_provider_invoice_by_provider_id(
        session,
        provider=InvoiceProvider.xrocket,
        provider_invoice_id=provider_invoice.invoice_id,
    )
    if invoice is None:
        msg = f"invoice for provider invoice {provider_invoice.invoice_id} was not found"
        raise LookupError(msg)

    if invoice.status is InvoiceStatus.paid:
        return _paid_result(
            processed=False,
            invoice=invoice,
            subscription=None,
            user=None,
            tariff=None,
        )

    if provider_invoice.status != "paid":
        msg = "provider invoice is not paid"
        raise ValueError(msg)
    if provider_invoice.payload != invoice.payload:
        msg = "provider invoice payload mismatch"
        raise ValueError(msg)

    if invoice.tariff_id is None:
        msg = "invoice tariff_id is missing"
        raise ValueError(msg)
    if invoice.id is None:
        msg = "invoice must have an id before payment processing"
        raise ValueError(msg)

    user = await _load_paid_invoice_user(session, invoice.user_id)
    tariff = await _load_paid_invoice_tariff(session, invoice.tariff_id)
    subscription = await extend_paid_subscription(
        session,
        user=user,
        days=tariff_duration_days(tariff.duration_months),
        source=SubscriptionSource.xrocket,
        last_invoice_id=invoice.id,
        now=now,
        bonus_days=invoice.promo_extra_days or 0,
        loyalty=settings.loyalty if settings is not None else None,
        plan_code=resolve_plan_code(settings, tariff.code) if settings is not None else None,
        settings=settings,
    )
    await mark_invoice_paid(
        session,
        invoice,
        paid_at=now,
        raw_payload=provider_invoice.raw,
    )
    await _record_paid_referral_earning(
        session,
        invoice=invoice,
        user=user,
        now=now,
        settings=settings,
    )
    return _paid_result(
        processed=True,
        invoice=invoice,
        subscription=subscription,
        user=user,
        tariff=tariff,
    )


async def process_paid_tribute_invoice(
    session: AsyncSession,
    *,
    event: TributeDigitalProductEvent,
    tariff_code: str,
    user: User,
    now: datetime,
    settings: Settings,
) -> PaidInvoiceResult:
    """Record a paid Tribute purchase and grant access.

    Unlike the polling providers, Tribute has no pre-created pending invoice (no create API,
    no echoed payload): the paid invoice is created here from the webhook event. Idempotency
    is keyed by ``purchase_id`` (the unique ``(provider, provider_invoice_id)`` index). Tribute
    charges a fixed price in EUR/RUB; USD accounting/referral use the mapped tariff's price
    (loyalty/promo do not apply to a Tribute payment).
    """
    provider_invoice_id = str(event.purchase_id)
    existing = await lock_provider_invoice_by_provider_id(
        session,
        provider=InvoiceProvider.tribute,
        provider_invoice_id=provider_invoice_id,
    )
    if existing is not None:
        tariff = (
            await _load_paid_invoice_tariff(session, existing.tariff_id)
            if existing.tariff_id is not None
            else None
        )
        subscription = await _load_subscription_for_user(session, user.id)
        return _paid_result(
            processed=False,
            invoice=existing,
            subscription=subscription,
            user=user,
            tariff=tariff,
        )

    tariff = await ensure_configured_tariff(session, settings, tariff_code)
    quote = quote_tariff(settings, tariff_code, user.status, fee_percent=Decimal("0"))
    provider_payload: dict[str, Any] = {
        "tribute_amount": event.amount,
        "tribute_currency": event.currency,
        "transaction_id": event.transaction_id,
        "product_id": event.product_id,
        "telegram_username": event.telegram_username,
        "event": event.raw,
    }
    invoice = await create_pending_provider_invoice(
        session,
        provider=InvoiceProvider.tribute,
        user=user,
        tariff_id=tariff.id,
        quote=quote,
        currency=event.currency or "EUR",
        expires_at=now,
        payload=f"tribute:{provider_invoice_id}",
    )
    invoice = await mark_invoice_provider_created(
        session,
        invoice,
        provider_invoice_id=provider_invoice_id,
        raw_payload=provider_payload,
    )
    if invoice.id is None:
        msg = "invoice must have an id before payment processing"
        raise ValueError(msg)

    subscription = await extend_paid_subscription(
        session,
        user=user,
        days=tariff_duration_days(tariff.duration_months),
        source=SubscriptionSource.tribute,
        last_invoice_id=invoice.id,
        now=now,
        bonus_days=0,
        loyalty=settings.loyalty,
        plan_code=resolve_plan_code(settings, tariff.code),
        settings=settings,
    )
    await mark_invoice_paid(session, invoice, paid_at=now, raw_payload=provider_payload)
    await _record_paid_referral_earning(
        session,
        invoice=invoice,
        user=user,
        now=now,
        settings=settings,
    )
    return _paid_result(
        processed=True,
        invoice=invoice,
        subscription=subscription,
        user=user,
        tariff=tariff,
    )


async def validate_stars_pre_checkout(
    session: AsyncSession,
    *,
    payload: str,
    currency: str,
    total_amount: int,
    now: datetime,
) -> StarsPreCheckoutResult:
    invoice = await lock_invoice_by_payload(
        session,
        provider=InvoiceProvider.stars,
        payload=payload,
    )
    if invoice is None:
        return StarsPreCheckoutResult(ok=False, error_message="invoice not found")
    if invoice.status is not InvoiceStatus.active:
        return StarsPreCheckoutResult(
            ok=False,
            invoice=invoice,
            error_message="invoice is not active",
        )
    if invoice.expires_at <= now:
        return StarsPreCheckoutResult(ok=False, invoice=invoice, error_message="invoice expired")
    if currency != STARS_CURRENCY:
        return StarsPreCheckoutResult(
            ok=False,
            invoice=invoice,
            error_message="unsupported currency",
        )

    expected_amount = _stars_amount_from_invoice(invoice)
    if expected_amount != total_amount:
        return StarsPreCheckoutResult(
            ok=False,
            invoice=invoice,
            error_message="invoice amount mismatch",
        )
    return StarsPreCheckoutResult(ok=True, invoice=invoice)


async def process_paid_stars_invoice(
    session: AsyncSession,
    *,
    successful_payment: StarsSuccessfulPayment,
    now: datetime,
    settings: Settings | None = None,
) -> PaidInvoiceResult:
    charged_invoice = await lock_invoice_by_telegram_payment_charge_id(
        session,
        successful_payment.telegram_payment_charge_id,
    )
    if charged_invoice is not None:
        return await _existing_paid_stars_result(session, charged_invoice)

    invoice = await lock_invoice_by_payload(
        session,
        provider=InvoiceProvider.stars,
        payload=successful_payment.invoice_payload,
    )
    if invoice is None:
        msg = f"Stars invoice for payload {successful_payment.invoice_payload} was not found"
        raise LookupError(msg)

    if invoice.status is InvoiceStatus.paid:
        if _stars_subscription_period_from_invoice(invoice) is None:
            msg = "Stars invoice is already paid"
            raise ValueError(msg)
        return await _process_paid_stars_recurring_invoice(
            session,
            parent_invoice=invoice,
            successful_payment=successful_payment,
            now=now,
            settings=settings,
        )

    if invoice.status is not InvoiceStatus.active:
        msg = "invoice is not active"
        raise ValueError(msg)
    _validate_successful_stars_payment(invoice, successful_payment, now=now)

    if invoice.tariff_id is None:
        msg = "invoice tariff_id is missing"
        raise ValueError(msg)
    if invoice.id is None:
        msg = "invoice must have an id before payment processing"
        raise ValueError(msg)

    user = await _load_paid_invoice_user(session, invoice.user_id)
    tariff = await _load_paid_invoice_tariff(session, invoice.tariff_id)
    subscription = await extend_paid_subscription(
        session,
        user=user,
        days=tariff_duration_days(tariff.duration_months),
        source=SubscriptionSource.stars,
        last_invoice_id=invoice.id,
        now=now,
        bonus_days=invoice.promo_extra_days or 0,
        loyalty=settings.loyalty if settings is not None else None,
        plan_code=resolve_plan_code(settings, tariff.code) if settings is not None else None,
        settings=settings,
    )
    if _stars_subscription_period_from_invoice(invoice) is not None:
        subscription.stars_subscription_active = True
    await mark_stars_invoice_paid(
        session,
        invoice,
        paid_at=now,
        telegram_payment_charge_id=successful_payment.telegram_payment_charge_id,
        raw_payload=successful_payment.raw,
    )
    await _record_paid_referral_earning(
        session,
        invoice=invoice,
        user=user,
        now=now,
        settings=settings,
    )
    return _paid_result(
        processed=True,
        invoice=invoice,
        subscription=subscription,
        user=user,
        tariff=tariff,
    )


async def _process_paid_stars_recurring_invoice(
    session: AsyncSession,
    *,
    parent_invoice: Invoice,
    successful_payment: StarsSuccessfulPayment,
    now: datetime,
    settings: Settings | None,
) -> PaidInvoiceResult:
    charged_invoice = await lock_invoice_by_telegram_payment_charge_id(
        session,
        successful_payment.telegram_payment_charge_id,
    )
    if charged_invoice is not None:
        return await _existing_paid_stars_result(session, charged_invoice)

    _validate_successful_stars_payment(parent_invoice, successful_payment, now=now)

    if parent_invoice.tariff_id is None:
        msg = "invoice tariff_id is missing"
        raise ValueError(msg)
    if parent_invoice.id is None:
        msg = "invoice must have an id before recurring payment processing"
        raise ValueError(msg)

    renewal_invoice = await create_paid_stars_recurring_invoice(
        session,
        parent_invoice=parent_invoice,
        payload=_stars_recurring_payload(successful_payment),
        paid_at=now,
        telegram_payment_charge_id=successful_payment.telegram_payment_charge_id,
        raw_payload=successful_payment.raw,
    )
    if renewal_invoice.id is None:
        msg = "recurring invoice must have an id after flush"
        raise ValueError(msg)

    user = await _load_paid_invoice_user(session, parent_invoice.user_id)
    tariff = await _load_paid_invoice_tariff(session, parent_invoice.tariff_id)
    subscription = await extend_paid_subscription(
        session,
        user=user,
        days=tariff_duration_days(tariff.duration_months),
        source=SubscriptionSource.stars,
        last_invoice_id=renewal_invoice.id,
        now=now,
        bonus_days=0,
        loyalty=settings.loyalty if settings is not None else None,
        plan_code=resolve_plan_code(settings, tariff.code) if settings is not None else None,
        settings=settings,
    )
    subscription.stars_subscription_active = True
    await _record_paid_referral_earning(
        session,
        invoice=renewal_invoice,
        user=user,
        now=now,
        settings=settings,
    )
    return _paid_result(
        processed=True,
        invoice=renewal_invoice,
        subscription=subscription,
        user=user,
        tariff=tariff,
    )


async def _prepare_local_invoice(
    session: AsyncSession,
    *,
    settings: Settings,
    user: User,
    tariff_code: str,
    now: datetime,
    provider: InvoiceProvider,
    currency: str,
    expires_seconds: int,
    fee_percent: Decimal,
) -> PreparedInvoice:
    locked_user, promo = await get_pending_promo_for_user(
        session,
        user=user,
        tariff_code=tariff_code,
        now=now,
        lock_user=True,
    )
    quote = quote_tariff(
        settings,
        tariff_code,
        locked_user.status,
        promo=promo_snapshot_from_row(promo) if promo is not None else None,
        fee_percent=fee_percent,
    )
    tariff = await ensure_configured_tariff(session, settings, tariff_code)
    payload = f"inv:{uuid.uuid4()}"
    if provider is InvoiceProvider.cryptobot:
        invoice = await create_pending_cryptobot_invoice(
            session,
            user=locked_user,
            tariff_id=tariff.id,
            quote=quote,
            expires_at=now + timedelta(seconds=expires_seconds),
            payload=payload,
        )
    else:
        invoice = await create_pending_provider_invoice(
            session,
            provider=provider,
            user=locked_user,
            tariff_id=tariff.id,
            quote=quote,
            currency=currency,
            expires_at=now + timedelta(seconds=expires_seconds),
            payload=payload,
        )
    if invoice.id is None:
        msg = "invoice must have an id after flush"
        raise ValueError(msg)
    if promo is not None:
        await reserve_promo_redemption(
            session,
            promo=promo,
            user=locked_user,
            invoice=invoice,
            now=now,
        )
    return PreparedInvoice(
        invoice_id=invoice.id,
        quote=quote,
        payload=payload,
        description=_invoice_description(quote),
    )


async def _create_provider_invoice(
    client: SupportsCreateCryptoBotInvoice,
    *,
    settings: Settings,
    prepared: PreparedInvoice,
    paid_btn_url: str,
) -> CryptoBotInvoice:
    try:
        provider_invoice = await client.create_invoice(
            amount=prepared.quote.gross_usd,
            payload=prepared.payload,
            description=prepared.description,
            paid_btn_url=paid_btn_url,
            accepted_assets=settings.cryptobot.accepted_assets,
            expires_in=settings.cryptobot.invoice_expires_seconds,
        )
    except CryptoBotAPIError:
        raise
    except Exception as exc:
        msg = "CryptoBot invoice creation failed"
        raise CryptoBotAPIError(msg) from exc

    if provider_invoice.pay_url is None or not provider_invoice.pay_url.strip():
        msg = "missing pay URL in CryptoBot invoice"
        raise CryptoBotAPIError(msg)
    return provider_invoice


async def _create_xrocket_provider_invoice(
    client: SupportsCreateXRocketInvoice,
    *,
    settings: Settings,
    prepared: PreparedInvoice,
) -> XRocketInvoice:
    try:
        provider_invoice = await client.create_invoice(
            amount=prepared.quote.gross_usd,
            payload=prepared.payload,
            description=prepared.description,
            currency=settings.xrocket.currency,
            expires_in=settings.xrocket.invoice_expires_seconds,
        )
    except XRocketAPIError:
        raise
    except Exception as exc:
        msg = "xRocket invoice creation failed"
        raise XRocketAPIError(msg) from exc

    if provider_invoice.pay_url is None or not provider_invoice.pay_url.strip():
        msg = "missing pay URL in xRocket invoice"
        raise XRocketAPIError(msg)
    return provider_invoice


async def _load_paid_invoice_user(session: AsyncSession, user_id: int) -> User:
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        msg = f"user with id {user_id} was not found"
        raise LookupError(msg)
    return user


async def _load_paid_invoice_tariff(session: AsyncSession, tariff_id: int) -> Tariff:
    tariff = await session.scalar(select(Tariff).where(Tariff.id == tariff_id))
    if tariff is None:
        msg = f"tariff with id {tariff_id} was not found"
        raise LookupError(msg)
    return tariff


@dataclass(frozen=True, slots=True)
class PaidAmounts:
    gross: Decimal | None = None
    fee: Decimal | None = None
    net: Decimal | None = None


def _paid_amounts_from_payload(raw_payload: dict[str, Any]) -> PaidAmounts:
    if raw_payload.get("currency_type") != "fiat" or raw_payload.get("fiat") != "USD":
        return PaidAmounts()

    gross = _decimal_or_none(raw_payload.get("amount"))
    fee = _paid_fee_usd(raw_payload)
    net = gross - fee if gross is not None and fee is not None else None
    return PaidAmounts(gross=gross, fee=fee, net=net)


def _paid_fee_usd(raw_payload: dict[str, Any]) -> Decimal | None:
    fee = _decimal_or_none(raw_payload.get("fee_amount"))
    paid_usd_rate = _decimal_or_none(raw_payload.get("paid_usd_rate"))
    if fee is None or paid_usd_rate is None:
        return None

    if raw_payload.get("fee_asset") != raw_payload.get("paid_asset"):
        return None
    return fee * paid_usd_rate


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _validate_successful_stars_payment(
    invoice: Invoice,
    payment: StarsSuccessfulPayment,
    *,
    now: datetime,
) -> None:
    del now
    if payment.currency != STARS_CURRENCY:
        msg = "unsupported currency"
        raise ValueError(msg)
    if payment.total_amount != _stars_amount_from_invoice(invoice):
        msg = "invoice amount mismatch"
        raise ValueError(msg)


def _stars_amount_from_invoice(invoice: Invoice) -> int:
    provider_payload = invoice.provider_payload or {}
    if "stars_amount" in provider_payload:
        try:
            return int(provider_payload["stars_amount"])
        except (TypeError, ValueError) as exc:
            msg = "invoice stars amount is invalid"
            raise ValueError(msg) from exc
    successful_payment = provider_payload.get("successful_payment")
    if isinstance(successful_payment, dict) and "total_amount" in successful_payment:
        try:
            return int(successful_payment["total_amount"])
        except (TypeError, ValueError) as exc:
            msg = "invoice stars amount is invalid"
            raise ValueError(msg) from exc
    try:
        return int(provider_payload["total_amount"])
    except (KeyError, TypeError, ValueError) as exc:
        msg = "invoice stars amount is missing"
        raise ValueError(msg) from exc


def _stars_subscription_period_from_invoice(invoice: Invoice) -> int | None:
    provider_payload = invoice.provider_payload or {}
    value = provider_payload.get("subscription_period")
    return int(value) if value is not None else None


def _stars_recurring_payload(payment: StarsSuccessfulPayment) -> str:
    key = f"{payment.invoice_payload}:{payment.telegram_payment_charge_id}"
    return f"stars:renew:{uuid.uuid5(uuid.NAMESPACE_URL, key)}"


async def _existing_paid_stars_result(
    session: AsyncSession,
    invoice: Invoice,
) -> PaidInvoiceResult:
    user = await _load_paid_invoice_user(session, invoice.user_id)
    tariff = None
    if invoice.tariff_id is not None:
        tariff = await _load_paid_invoice_tariff(session, invoice.tariff_id)
    subscription = await _load_subscription_for_user(session, invoice.user_id)
    return _paid_result(
        processed=False,
        invoice=invoice,
        subscription=subscription,
        user=user,
        tariff=tariff,
    )


async def _load_subscription_for_user(
    session: AsyncSession,
    user_id: int,
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id).with_for_update(),
    )


async def _record_paid_referral_earning(
    session: AsyncSession,
    *,
    invoice: Invoice,
    user: User,
    now: datetime,
    settings: Settings | None,
) -> None:
    if invoice.id is None or user.id is None:
        return
    referral = await get_referral_by_referee_id(session, user.id)
    if referral is None:
        return

    percent = (
        referral.custom_percent
        if referral.custom_percent is not None
        else settings.referral.percent_default
        if settings is not None
        else Decimal("10")
    )
    await record_referral_earning(
        session,
        referral_id=referral.id or 0,
        referrer_id=referral.referrer_id,
        referee_id=referral.referee_id,
        invoice_id=invoice.id,
        invoice_net_usd=invoice.net,
        percent=percent,
        created_at=now,
    )


def _paid_result(
    *,
    processed: bool,
    invoice: Invoice,
    subscription: Subscription | None,
    user: User | None,
    tariff: Tariff | None,
) -> PaidInvoiceResult:
    return PaidInvoiceResult(
        processed=processed,
        invoice=invoice,
        subscription=subscription,
        invoice_id=invoice.id,
        provider_invoice_id=invoice.provider_invoice_id,
        user_tg_id=user.tg_id if user is not None else None,
        username=user.username if user is not None else None,
        lang_code=user.lang_code if user is not None else None,
        tariff_code=tariff.code if tariff is not None else None,
        gross=invoice.gross,
        fee=invoice.fee,
        net=invoice.net,
        currency=invoice.currency,
        subscription_expires_at=subscription.expires_at if subscription is not None else None,
        plan_code=subscription.plan_code if subscription is not None else None,
    )


async def cancel_unusable_invoice(
    session_factory: async_sessionmaker[AsyncSession],
    invoice_id: int,
) -> None:
    try:
        async with session_scope(session_factory) as session:
            invoice = await lock_invoice_by_id(session, invoice_id)
            if (
                invoice is not None
                and invoice.status is InvoiceStatus.active
                and invoice.provider_invoice_id is None
            ):
                await release_promo_redemption_for_invoice(session, invoice)
                invoice.status = InvoiceStatus.cancelled
    except Exception:
        logger.exception(
            "failed to cancel unusable invoice",
            extra={"invoice_id": invoice_id},
        )


_cancel_unusable_invoice = cancel_unusable_invoice


def _invoice_description(quote: TariffQuote) -> str:
    month_label = "month" if quote.tariff.duration_months == 1 else "months"
    return f"Hydra Monitors access: {quote.tariff.duration_months} {month_label}"
