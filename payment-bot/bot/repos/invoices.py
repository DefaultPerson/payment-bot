from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Invoice, InvoiceProvider, InvoiceStatus, User


class SupportsInvoiceQuote(Protocol):
    gross_usd: Decimal
    fee_usd: Decimal
    net_usd: Decimal


async def create_pending_cryptobot_invoice(
    session: AsyncSession,
    *,
    user: User,
    tariff_id: int,
    quote: SupportsInvoiceQuote,
    expires_at: datetime,
    payload: str,
) -> Invoice:
    invoice = Invoice(
        user_id=user.id,
        provider=InvoiceProvider.cryptobot,
        provider_invoice_id=None,
        telegram_payment_charge_id=None,
        tariff_id=tariff_id,
        amount_usd=quote.gross_usd,
        gross=quote.gross_usd,
        fee=quote.fee_usd,
        net=quote.net_usd,
        currency="USD",
        status=InvoiceStatus.active,
        payload=payload,
        provider_payload=None,
        expires_at=expires_at,
        paid_at=None,
    )
    session.add(invoice)
    await session.flush()
    return invoice


async def mark_invoice_provider_created(
    session: AsyncSession,
    invoice: Invoice,
    *,
    provider_invoice_id: str,
    raw_payload: dict[str, Any],
) -> Invoice:
    if (
        invoice.provider_invoice_id is not None
        and invoice.provider_invoice_id != provider_invoice_id
    ):
        msg = "provider invoice id already set"
        raise ValueError(msg)

    invoice.provider_invoice_id = provider_invoice_id
    invoice.provider_payload = raw_payload
    await session.flush()
    return invoice


async def get_active_cryptobot_invoices(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
) -> list[Invoice]:
    result = await session.scalars(
        select(Invoice)
        .where(
            Invoice.provider == InvoiceProvider.cryptobot,
            Invoice.status == InvoiceStatus.active,
            Invoice.provider_invoice_id.is_not(None),
            Invoice.expires_at > now,
        )
        .order_by(Invoice.id.asc(), Invoice.created_at.asc())
        .limit(limit),
    )
    return list(result.all())


async def get_active_cryptobot_invoices_after_id(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
    after_id: int | None = None,
) -> list[Invoice]:
    statement = (
        select(Invoice)
        .where(
            Invoice.provider == InvoiceProvider.cryptobot,
            Invoice.status == InvoiceStatus.active,
            Invoice.provider_invoice_id.is_not(None),
            Invoice.expires_at > now,
        )
        .order_by(Invoice.id.asc())
        .limit(limit)
    )
    if after_id is not None:
        statement = statement.where(Invoice.id > after_id)

    result = await session.scalars(statement)
    return list(result.all())


async def lock_invoice_by_provider_id(
    session: AsyncSession,
    provider_invoice_id: str,
) -> Invoice | None:
    return await session.scalar(
        select(Invoice)
        .where(
            Invoice.provider == InvoiceProvider.cryptobot,
            Invoice.provider_invoice_id == provider_invoice_id,
        )
        .with_for_update(),
    )


async def lock_invoice_by_id(session: AsyncSession, invoice_id: int) -> Invoice | None:
    return await session.scalar(
        select(Invoice).where(Invoice.id == invoice_id).with_for_update(),
    )


async def mark_invoice_paid(
    session: AsyncSession,
    invoice: Invoice,
    *,
    paid_at: datetime,
    raw_payload: dict[str, Any],
    gross: Decimal | None = None,
    fee: Decimal | None = None,
    net: Decimal | None = None,
) -> Invoice:
    invoice.status = InvoiceStatus.paid
    if invoice.paid_at is None:
        invoice.paid_at = paid_at
    invoice.provider_payload = raw_payload
    if gross is not None:
        invoice.gross = gross
        invoice.amount_usd = gross
    if fee is not None:
        invoice.fee = fee
    if net is not None:
        invoice.net = net
    await session.flush()
    return invoice


async def mark_expired_cryptobot_invoices(
    session: AsyncSession,
    *,
    now: datetime,
) -> int:
    result = await session.scalars(
        select(Invoice)
        .where(
            Invoice.provider == InvoiceProvider.cryptobot,
            Invoice.status == InvoiceStatus.active,
            Invoice.expires_at <= now,
        )
        .order_by(Invoice.id.asc()),
    )
    invoices = list(result.all())
    for invoice in invoices:
        invoice.status = InvoiceStatus.expired
    await session.flush()
    return len(invoices)
