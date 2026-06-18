from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.models import Invoice, InvoiceProvider
from bot.payments.xrocket import XRocketInvoice
from bot.repos.invoices import (
    get_active_provider_invoices_after_id,
    mark_expired_provider_invoices,
)
from bot.repos.provider_events import record_provider_event
from bot.services.access import send_access_links
from bot.services.payments import PaidInvoiceResult, process_paid_xrocket_invoice

logger = logging.getLogger(__name__)


class SupportsXRocketPolling(Protocol):
    async def get_invoice(self, invoice_id: str) -> XRocketInvoice: ...


class SupportsAccessDelivery(Protocol):
    async def create_chat_invite_link(
        self,
        *,
        chat_id: int,
        name: str,
        expire_date: datetime,
        member_limit: int,
        creates_join_request: bool,
    ) -> object: ...

    async def send_message(self, *, chat_id: int, text: str) -> object: ...


@dataclass(frozen=True, slots=True)
class XRocketPollResult:
    polled: int
    paid: int
    expired: int
    errors: int
    undelivered_access: tuple[PaidInvoiceResult, ...] = ()


@dataclass(frozen=True, slots=True)
class ActiveXRocketInvoiceRef:
    invoice_id: int
    provider_invoice_id: str


@dataclass(frozen=True, slots=True)
class ProviderInvoiceResult:
    invoice: XRocketInvoice | None
    errors: int = 0


@dataclass(frozen=True, slots=True)
class SideEffectDeliveryResult:
    errors: int = 0
    retry_access: bool = False


async def poll_xrocket_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: SupportsXRocketPolling,
    bot: SupportsAccessDelivery,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    now: datetime | None = None,
    retry_access: Sequence[PaidInvoiceResult] = (),
) -> XRocketPollResult:
    now = now or datetime.now(UTC)
    retry_errors, undelivered_access = await _retry_access_deliveries(
        retry_access,
        bot=bot,
        settings=settings,
        i18n=i18n,
        now=now,
    )
    active_refs = await _load_active_invoice_refs(
        session_factory,
        now=now,
        batch_size=settings.xrocket.poll_batch_size,
    )

    polled = 0
    paid = 0
    errors = retry_errors
    for invoice_ref in active_refs:
        provider_result = await _fetch_provider_invoice(client, invoice_ref)
        errors += provider_result.errors
        provider_invoice = provider_result.invoice
        if provider_invoice is None:
            continue

        polled += 1
        event_recorded = await _record_provider_invoice_event(
            session_factory,
            provider_invoice=provider_invoice,
            now=now,
        )
        if not event_recorded:
            errors += 1
            continue

        if provider_invoice.status != "paid":
            continue

        paid_result = await _process_provider_paid_invoice(
            session_factory,
            settings=settings,
            provider_invoice=provider_invoice,
            now=now,
        )
        if paid_result is None:
            errors += 1
            continue
        if not paid_result.processed:
            continue

        paid += 1
        side_effects = await _deliver_paid_side_effects(
            paid_result,
            bot=bot,
            settings=settings,
            i18n=i18n,
            service_channel=service_channel,
            now=now,
        )
        errors += side_effects.errors
        if side_effects.retry_access:
            undelivered_access.append(paid_result)

    expired = await _mark_expired(session_factory, now=now)
    return XRocketPollResult(
        polled=polled,
        paid=paid,
        expired=expired,
        errors=errors,
        undelivered_access=tuple(undelivered_access),
    )


async def xrocket_poller_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: SupportsXRocketPolling,
    bot: SupportsAccessDelivery,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    sleep: Callable[[int], Awaitable[None]] = asyncio.sleep,
    max_iterations: int | None = None,
) -> None:
    iterations = 0
    retry_access: tuple[PaidInvoiceResult, ...] = ()
    while True:
        try:
            result = await poll_xrocket_once(
                session_factory=session_factory,
                settings=settings,
                client=client,
                bot=bot,
                i18n=i18n,
                service_channel=service_channel,
                retry_access=retry_access,
            )
            retry_access = result.undelivered_access
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("xrocket poll iteration failed")

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        await sleep(settings.xrocket.poll_interval_seconds)


async def _load_active_invoice_refs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    batch_size: int,
) -> list[ActiveXRocketInvoiceRef]:
    refs: list[ActiveXRocketInvoiceRef] = []
    after_id: int | None = None
    async with session_scope(session_factory) as session:
        while True:
            invoices = await get_active_provider_invoices_after_id(
                session,
                provider=InvoiceProvider.xrocket,
                now=now,
                limit=batch_size,
                after_id=after_id,
            )
            if not invoices:
                break

            refs.extend(_invoice_ref(invoice) for invoice in invoices)
            after_id = max(ref.invoice_id for ref in refs)
            if len(invoices) < batch_size:
                break

    return refs


def _invoice_ref(invoice: Invoice) -> ActiveXRocketInvoiceRef:
    if invoice.id is None:
        msg = "active invoice id is missing"
        raise ValueError(msg)
    if invoice.provider_invoice_id is None:
        msg = "active invoice provider id is missing"
        raise ValueError(msg)
    return ActiveXRocketInvoiceRef(
        invoice_id=invoice.id,
        provider_invoice_id=invoice.provider_invoice_id,
    )


async def _fetch_provider_invoice(
    client: SupportsXRocketPolling,
    invoice_ref: ActiveXRocketInvoiceRef,
) -> ProviderInvoiceResult:
    try:
        return ProviderInvoiceResult(
            invoice=await client.get_invoice(invoice_ref.provider_invoice_id),
        )
    except Exception:
        logger.exception(
            "failed to poll xRocket invoice",
            extra={"provider_invoice_id": invoice_ref.provider_invoice_id},
        )
        return ProviderInvoiceResult(invoice=None, errors=1)


async def _record_provider_invoice_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_invoice: XRocketInvoice,
    now: datetime,
) -> bool:
    try:
        async with session_scope(session_factory) as session:
            await record_provider_event(
                session,
                provider=InvoiceProvider.xrocket,
                external_id=provider_invoice.invoice_id,
                raw_payload=provider_invoice.raw,
                now=now,
            )
    except Exception:
        logger.exception(
            "failed to record xRocket provider event",
            extra={"provider_invoice_id": provider_invoice.invoice_id},
        )
        return False
    return True


async def _process_provider_paid_invoice(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    provider_invoice: XRocketInvoice,
    now: datetime,
) -> PaidInvoiceResult | None:
    try:
        async with session_scope(session_factory) as session:
            return await process_paid_xrocket_invoice(
                session,
                provider_invoice=provider_invoice,
                now=now,
                settings=settings,
            )
    except Exception:
        logger.exception(
            "failed to process paid xRocket invoice",
            extra={"provider_invoice_id": provider_invoice.invoice_id},
        )
        return None


async def _mark_expired(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> int:
    async with session_scope(session_factory) as session:
        return await mark_expired_provider_invoices(
            session,
            provider=InvoiceProvider.xrocket,
            now=now,
        )


async def _deliver_paid_side_effects(
    result: PaidInvoiceResult,
    *,
    bot: SupportsAccessDelivery,
    settings: Settings,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    now: datetime,
) -> SideEffectDeliveryResult:
    errors = 0
    if not await _notify_service_channel(service_channel, result):
        errors += 1

    access_sent = await _send_access(result, bot=bot, settings=settings, i18n=i18n, now=now)
    if not access_sent:
        errors += 1
    return SideEffectDeliveryResult(errors=errors, retry_access=not access_sent)


async def _retry_access_deliveries(
    results: Sequence[PaidInvoiceResult],
    *,
    bot: SupportsAccessDelivery,
    settings: Settings,
    i18n: JsonI18n,
    now: datetime,
) -> tuple[int, list[PaidInvoiceResult]]:
    errors = 0
    undelivered: list[PaidInvoiceResult] = []
    for result in results:
        if await _send_access(result, bot=bot, settings=settings, i18n=i18n, now=now):
            continue
        errors += 1
        undelivered.append(result)
    return errors, undelivered


async def _notify_service_channel(
    service_channel: ServiceChannel,
    result: PaidInvoiceResult,
) -> bool:
    try:
        return await service_channel.send_html(_payment_event_text(result))
    except Exception:
        logger.exception(
            "failed to send xRocket payment event",
            extra={"invoice_id": result.invoice_id},
        )
        return False


async def _send_access(
    result: PaidInvoiceResult,
    *,
    bot: SupportsAccessDelivery,
    settings: Settings,
    i18n: JsonI18n,
    now: datetime,
) -> bool:
    if result.user_tg_id is None or result.lang_code is None:
        logger.error(
            "paid xRocket invoice result is missing user delivery data",
            extra={"invoice_id": result.invoice_id},
        )
        return False
    try:
        await send_access_links(
            bot,
            settings,
            i18n,
            tg_id=result.user_tg_id,
            lang_code=result.lang_code,
            now=now,
        )
        return True
    except Exception:
        logger.exception(
            "failed to send xRocket access links",
            extra={"invoice_id": result.invoice_id, "user_tg_id": result.user_tg_id},
        )
        return False


def _payment_event_text(result: PaidInvoiceResult) -> str:
    return (
        "<b>xrocket payment paid</b>\n"
        f"user_tg_id={result.user_tg_id}\n"
        f"invoice_id={result.invoice_id}\n"
        f"provider_invoice_id={escape(str(result.provider_invoice_id))}\n"
        f"tariff={escape(str(result.tariff_code))}\n"
        f"gross={_amount(result.gross)} {escape(str(result.currency or 'USDT'))}\n"
        f"fee={_amount(result.fee)} {escape(str(result.currency or 'USDT'))}\n"
        f"net={_amount(result.net)} {escape(str(result.currency or 'USDT'))}\n"
        f"expires_at={_format_datetime(result.subscription_expires_at)}"
    )


def _amount(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else "unknown"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
