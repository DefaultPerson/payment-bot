from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.metrics import PaymentBotMetrics
from bot.core.service_events import fmt_amount, fmt_dt, money, render_event, user_mention
from bot.models import Invoice, InvoiceProvider
from bot.payments.cryptobot import CryptoBotInvoice
from bot.repos.invoices import (
    get_active_cryptobot_invoices_after_id,
    mark_expired_cryptobot_invoices,
)
from bot.repos.provider_events import record_provider_event
from bot.services.access import send_access_links
from bot.services.payments import PaidInvoiceResult, process_paid_cryptobot_invoice

logger = logging.getLogger(__name__)


class SupportsCryptoBotPolling(Protocol):
    async def get_invoices(
        self,
        invoice_ids: list[str],
        status: str | None = None,
    ) -> list[CryptoBotInvoice]: ...


class SupportsAccessDelivery(Protocol):
    async def create_chat_invite_link(
        self,
        *,
        chat_id: int,
        name: str,
        expire_date: datetime,
        creates_join_request: bool,
    ) -> object: ...

    async def send_message(self, *, chat_id: int, text: str) -> object: ...


@dataclass(frozen=True, slots=True)
class CryptoPollResult:
    polled: int
    paid: int
    expired: int
    errors: int
    undelivered_access: tuple[PaidInvoiceResult, ...] = ()


@dataclass(frozen=True, slots=True)
class ActiveCryptoInvoiceRef:
    invoice_id: int
    provider_invoice_id: str


@dataclass(frozen=True, slots=True)
class ProviderBatchResult:
    invoices: list[CryptoBotInvoice]
    errors: int = 0


@dataclass(frozen=True, slots=True)
class SideEffectDeliveryResult:
    errors: int = 0
    retry_access: bool = False


async def poll_cryptobot_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: SupportsCryptoBotPolling,
    bot: SupportsAccessDelivery,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    metrics: PaymentBotMetrics | None = None,
    now: datetime | None = None,
    retry_access: Sequence[PaidInvoiceResult] = (),
) -> CryptoPollResult:
    now = now or datetime.now(UTC)
    batch_size = settings.cryptobot.poll_batch_size
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
        batch_size=batch_size,
    )

    polled = 0
    paid = 0
    errors = retry_errors
    for batch in _chunks(active_refs, batch_size):
        provider_batch = await _fetch_provider_batch(client, batch)
        if metrics is not None:
            metrics.record_provider_poll(
                provider="cryptobot",
                result="error" if provider_batch.errors else "success",
            )
        errors += provider_batch.errors
        provider_invoices = provider_batch.invoices
        polled += len(provider_invoices)
        for provider_invoice in provider_invoices:
            event_recorded = await _record_provider_invoice_event(
                session_factory,
                provider_invoice=provider_invoice,
                now=now,
            )
            if not event_recorded:
                errors += 1
                if metrics is not None and provider_invoice.status == "paid":
                    metrics.record_payment(provider="cryptobot", status="error")
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
                if metrics is not None:
                    metrics.record_payment(provider="cryptobot", status="error")
                continue
            if not paid_result.processed:
                if metrics is not None:
                    metrics.record_payment(provider="cryptobot", status="duplicate")
                continue

            paid += 1
            if metrics is not None:
                metrics.record_payment(
                    provider="cryptobot",
                    status="paid",
                    amount_usd=paid_result.gross,
                )
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
    return CryptoPollResult(
        polled=polled,
        paid=paid,
        expired=expired,
        errors=errors,
        undelivered_access=tuple(undelivered_access),
    )


async def crypto_poller_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: SupportsCryptoBotPolling,
    bot: SupportsAccessDelivery,
    i18n: JsonI18n,
    service_channel: ServiceChannel,
    metrics: PaymentBotMetrics | None = None,
    sleep: Callable[[int], Awaitable[None]] = asyncio.sleep,
    max_iterations: int | None = None,
) -> None:
    iterations = 0
    retry_access: tuple[PaidInvoiceResult, ...] = ()
    while True:
        try:
            result = await poll_cryptobot_once(
                session_factory=session_factory,
                settings=settings,
                client=client,
                bot=bot,
                i18n=i18n,
                service_channel=service_channel,
                metrics=metrics,
                retry_access=retry_access,
            )
            retry_access = result.undelivered_access
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cryptobot poll iteration failed")

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        await sleep(settings.cryptobot.poll_interval_seconds)


async def _load_active_invoice_refs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    batch_size: int,
) -> list[ActiveCryptoInvoiceRef]:
    refs: list[ActiveCryptoInvoiceRef] = []
    after_id: int | None = None
    async with session_scope(session_factory) as session:
        while True:
            invoices = await get_active_cryptobot_invoices_after_id(
                session,
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


def _invoice_ref(invoice: Invoice) -> ActiveCryptoInvoiceRef:
    if invoice.id is None:
        msg = "active invoice id is missing"
        raise ValueError(msg)
    if invoice.provider_invoice_id is None:
        msg = "active invoice provider id is missing"
        raise ValueError(msg)
    return ActiveCryptoInvoiceRef(
        invoice_id=invoice.id,
        provider_invoice_id=invoice.provider_invoice_id,
    )


def _chunks(
    invoices: Sequence[ActiveCryptoInvoiceRef],
    batch_size: int,
) -> list[list[ActiveCryptoInvoiceRef]]:
    return [
        list(invoices[index : index + batch_size]) for index in range(0, len(invoices), batch_size)
    ]


async def _fetch_provider_batch(
    client: SupportsCryptoBotPolling,
    batch: list[ActiveCryptoInvoiceRef],
) -> ProviderBatchResult:
    invoice_ids = [invoice.provider_invoice_id for invoice in batch]
    try:
        return ProviderBatchResult(invoices=await client.get_invoices(invoice_ids))
    except Exception:
        logger.exception(
            "failed to poll CryptoBot invoices",
            extra={"provider_invoice_ids": invoice_ids},
        )
        return ProviderBatchResult(invoices=[], errors=1)


async def _record_provider_invoice_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_invoice: CryptoBotInvoice,
    now: datetime,
) -> bool:
    try:
        async with session_scope(session_factory) as session:
            await record_provider_event(
                session,
                provider=InvoiceProvider.cryptobot,
                external_id=provider_invoice.invoice_id,
                raw_payload=provider_invoice.raw,
                now=now,
            )
    except Exception:
        logger.exception(
            "failed to record CryptoBot provider event",
            extra={"provider_invoice_id": provider_invoice.invoice_id},
        )
        return False
    return True


async def _process_provider_paid_invoice(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    provider_invoice: CryptoBotInvoice,
    now: datetime,
) -> PaidInvoiceResult | None:
    try:
        async with session_scope(session_factory) as session:
            return await process_paid_cryptobot_invoice(
                session,
                provider_invoice=provider_invoice,
                now=now,
                settings=settings,
            )
    except Exception:
        logger.exception(
            "failed to process paid CryptoBot invoice",
            extra={"provider_invoice_id": provider_invoice.invoice_id},
        )
        return None


async def _mark_expired(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> int:
    async with session_scope(session_factory) as session:
        return await mark_expired_cryptobot_invoices(session, now=now)


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
            "failed to send CryptoBot payment event",
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
            "paid CryptoBot invoice result is missing user delivery data",
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
            plan_code=result.plan_code,
        )
        return True
    except Exception:
        logger.exception(
            "failed to send CryptoBot access links",
            extra={"invoice_id": result.invoice_id, "user_tg_id": result.user_tg_id},
        )
        return False


def _payment_event_text(result: PaidInvoiceResult) -> str:
    currency = result.currency or "USD"
    return render_event(
        glyph="coin",
        title="Cryptobot payment paid",
        fields=[
            ("User", user_mention(result.user_tg_id, result.username)),
            ("Payment", money(result.gross, currency)),
        ],
        extra={
            "provider_invoice_id": result.provider_invoice_id,
            "tariff": result.tariff_code,
            "gross": f"{fmt_amount(result.gross)} {currency}",
            "fee": f"{fmt_amount(result.fee)} {currency}",
            "net": f"{fmt_amount(result.net)} {currency}",
            "expires_at": fmt_dt(result.subscription_expires_at),
        },
    )
