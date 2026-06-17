from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.models import Invoice, User
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotInvoice
from bot.repos.invoices import create_pending_cryptobot_invoice, mark_invoice_provider_created
from bot.repos.tariffs import ensure_configured_tariff
from bot.services.pricing import TariffQuote, quote_tariff


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


async def create_cryptobot_invoice_for_user(
    session: AsyncSession,
    settings: Settings,
    client: SupportsCreateCryptoBotInvoice,
    user: User,
    tariff_code: str,
    paid_btn_url: str,
    now: datetime,
) -> CreatedInvoice:
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

    provider_invoice = await client.create_invoice(
        amount=quote.gross_usd,
        payload=payload,
        description=_invoice_description(quote),
        paid_btn_url=paid_btn_url,
        accepted_assets=settings.cryptobot.accepted_assets,
        expires_in=settings.cryptobot.invoice_expires_seconds,
    )
    if provider_invoice.pay_url is None:
        msg = "missing pay URL in CryptoBot invoice"
        raise CryptoBotAPIError(msg)

    invoice = await mark_invoice_provider_created(
        session,
        invoice,
        provider_invoice_id=provider_invoice.invoice_id,
        raw_payload=provider_invoice.raw,
    )
    return CreatedInvoice(invoice=invoice, quote=quote, pay_url=provider_invoice.pay_url)


def _invoice_description(quote: TariffQuote) -> str:
    month_label = "month" if quote.tariff.duration_months == 1 else "months"
    return f"Hydra Monitors access: {quote.tariff.duration_months} {month_label}"
