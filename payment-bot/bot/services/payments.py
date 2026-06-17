from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.models import Invoice, InvoiceStatus, Subscription, SubscriptionSource, Tariff, User
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotInvoice
from bot.repos.invoices import (
    create_pending_cryptobot_invoice,
    lock_invoice_by_id,
    lock_invoice_by_provider_id,
    mark_invoice_paid,
    mark_invoice_provider_created,
)
from bot.repos.subscriptions import extend_paid_subscription
from bot.repos.tariffs import ensure_configured_tariff
from bot.services.pricing import TariffQuote, quote_tariff, tariff_duration_days

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


@dataclass(frozen=True, slots=True)
class CreatedInvoice:
    invoice: Invoice
    quote: TariffQuote
    pay_url: str


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
        )

    try:
        provider_invoice = await _create_provider_invoice(
            client,
            settings=settings,
            prepared=prepared,
            paid_btn_url=paid_btn_url,
        )
    except CryptoBotAPIError:
        await _cancel_unusable_invoice(session_factory, prepared.invoice_id)
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


async def process_paid_cryptobot_invoice(
    session: AsyncSession,
    *,
    provider_invoice: CryptoBotInvoice,
    now: datetime,
) -> PaidInvoiceResult:
    invoice = await lock_invoice_by_provider_id(session, provider_invoice.invoice_id)
    if invoice is None:
        msg = f"invoice for provider invoice {provider_invoice.invoice_id} was not found"
        raise LookupError(msg)

    if invoice.status is InvoiceStatus.paid:
        return PaidInvoiceResult(processed=False, invoice=invoice, subscription=None)

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
    )
    await mark_invoice_paid(
        session,
        invoice,
        paid_at=now,
        raw_payload=provider_invoice.raw,
    )
    return PaidInvoiceResult(processed=True, invoice=invoice, subscription=subscription)


async def _prepare_local_invoice(
    session: AsyncSession,
    *,
    settings: Settings,
    user: User,
    tariff_code: str,
    now: datetime,
) -> PreparedInvoice:
    quote = quote_tariff(settings, tariff_code, user.status)
    tariff = await ensure_configured_tariff(session, settings, tariff_code)
    payload = f"inv:{uuid.uuid4()}"
    invoice = await create_pending_cryptobot_invoice(
        session,
        user=user,
        tariff_id=tariff.id,
        quote=quote,
        expires_at=now + timedelta(seconds=settings.cryptobot.invoice_expires_seconds),
        payload=payload,
    )
    if invoice.id is None:
        msg = "invoice must have an id after flush"
        raise ValueError(msg)
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

    if provider_invoice.pay_url is None:
        msg = "missing pay URL in CryptoBot invoice"
        raise CryptoBotAPIError(msg)
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


async def _cancel_unusable_invoice(
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
                invoice.status = InvoiceStatus.cancelled
    except Exception:
        logger.exception(
            "failed to cancel unusable CryptoBot invoice",
            extra={"invoice_id": invoice_id},
        )


def _invoice_description(quote: TariffQuote) -> str:
    month_label = "month" if quote.tariff.duration_months == 1 else "months"
    return f"Hydra Monitors access: {quote.tariff.duration_months} {month_label}"
