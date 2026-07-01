from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.metrics import PaymentBotMetrics
from bot.core.service_events import fmt_amount, fmt_dt, money, render_event, user_mention
from bot.models import InvoiceProvider
from bot.payments.tribute import (
    SIGNATURE_HEADER,
    TributeDigitalProductEvent,
    parse_digital_product_event,
    verify_signature,
)
from bot.repos.provider_events import record_provider_event
from bot.repos.users import ensure_minimal_user
from bot.services.access import send_access_links
from bot.services.payments import PaidInvoiceResult, process_paid_tribute_invoice

logger = logging.getLogger(__name__)

WebHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]

# Tribute amounts arrive in the currency's minor units (e.g. euro cents).
_MINOR_UNITS = Decimal("100")


@dataclass(slots=True)
class TributeWebhookDeps:
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    bot: Bot
    i18n: JsonI18n
    service_channel: ServiceChannel
    metrics: PaymentBotMetrics | None = None


def create_tribute_webhook_handler(deps: TributeWebhookDeps) -> WebHandler:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _handle(deps, request)

    return handler


async def _handle(deps: TributeWebhookDeps, request: web.Request) -> web.StreamResponse:
    body = await request.read()
    token = deps.settings.tribute.token
    signature = request.headers.get(SIGNATURE_HEADER)
    if token is None or not verify_signature(token.get_secret_value(), body, signature):
        logger.warning("tribute webhook rejected: invalid signature")
        return web.Response(status=401)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400)
    if not isinstance(data, dict):
        return web.Response(status=400)

    event = parse_digital_product_event(data)
    if event is None:
        return web.Response(status=200)  # ignore non-purchase Tribute events

    tariff_code = deps.settings.tribute.tariff_for_product(event.product_id)
    if tariff_code is None:
        logger.info("tribute webhook: unmapped product_id=%s", event.product_id)
        return web.Response(status=200)

    now = datetime.now(UTC)
    try:
        result = await _process(deps, event, tariff_code, now)
    except Exception:
        logger.exception(
            "tribute webhook processing failed",
            extra={"purchase_id": event.purchase_id},
        )
        return web.Response(status=500)

    if not result.processed:
        if deps.metrics is not None:
            deps.metrics.record_payment(provider="tribute", status="duplicate")
        return web.Response(status=200)

    if deps.metrics is not None:
        deps.metrics.record_payment(provider="tribute", status="paid", amount_usd=result.gross)
    await _notify_service_channel(deps.service_channel, result, event)
    await _send_access(deps, result, now)
    return web.Response(status=200)


async def _process(
    deps: TributeWebhookDeps,
    event: TributeDigitalProductEvent,
    tariff_code: str,
    now: datetime,
) -> PaidInvoiceResult:
    async with session_scope(deps.session_factory) as session:
        user = await ensure_minimal_user(session, tg_id=event.telegram_user_id, i18n=deps.i18n)
        await record_provider_event(
            session,
            provider=InvoiceProvider.tribute,
            external_id=str(event.transaction_id),
            raw_payload=event.raw,
            now=now,
        )
        return await process_paid_tribute_invoice(
            session,
            event=event,
            tariff_code=tariff_code,
            user=user,
            now=now,
            settings=deps.settings,
        )


async def _send_access(
    deps: TributeWebhookDeps,
    result: PaidInvoiceResult,
    now: datetime,
) -> None:
    if result.user_tg_id is None or result.lang_code is None:
        logger.error(
            "paid Tribute invoice result is missing user delivery data",
            extra={"invoice_id": result.invoice_id},
        )
        return
    try:
        await send_access_links(
            deps.bot,
            deps.settings,
            deps.i18n,
            tg_id=result.user_tg_id,
            lang_code=result.lang_code,
            now=now,
            plan_code=result.plan_code,
        )
    except Exception:
        logger.exception(
            "failed to send Tribute access links",
            extra={"invoice_id": result.invoice_id, "user_tg_id": result.user_tg_id},
        )


async def _notify_service_channel(
    service_channel: ServiceChannel,
    result: PaidInvoiceResult,
    event: TributeDigitalProductEvent,
) -> None:
    try:
        await service_channel.send_html(_payment_event_text(result, event))
    except Exception:
        logger.exception(
            "failed to send Tribute payment event",
            extra={"invoice_id": result.invoice_id},
        )


def _payment_event_text(result: PaidInvoiceResult, event: TributeDigitalProductEvent) -> str:
    currency = event.currency or "EUR"
    amount = Decimal(event.amount) / _MINOR_UNITS
    return render_event(
        glyph="coin",
        title="Tribute payment paid",
        fields=[
            ("User", user_mention(result.user_tg_id, result.username)),
            ("Payment", money(amount, currency)),
        ],
        extra={
            "tariff": result.tariff_code,
            "product_id": event.product_id,
            "purchase_id": event.purchase_id,
            "amount": f"{fmt_amount(amount)} {currency}",
            "net_usd": f"{fmt_amount(result.net)} USD",
            "expires_at": fmt_dt(result.subscription_expires_at),
        },
    )
