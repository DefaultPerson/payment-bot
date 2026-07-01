"""Inbound payment webhooks for CryptoBot and xRocket.

These providers already settle via background pollers; the webhook is an additional, faster
push path. Both are kept: the poller stays as a fallback so a dropped/mis-signed webhook never
loses a payment. Idempotency is guaranteed by ``process_paid_*_invoice`` (lock by
``provider_invoice_id`` + already-paid short-circuit), so webhook and poller processing the same
invoice is safe. Signature scheme for both: HMAC-SHA256(raw_body) keyed by SHA256(app token).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.metrics import PaymentBotMetrics
from bot.jobs.crypto_poller import _deliver_paid_side_effects as _cryptobot_deliver
from bot.jobs.xrocket_poller import _deliver_paid_side_effects as _xrocket_deliver
from bot.models import InvoiceProvider
from bot.payments import cryptobot, xrocket
from bot.repos.provider_events import record_provider_event
from bot.services.payments import (
    PaidInvoiceResult,
    process_paid_cryptobot_invoice,
    process_paid_xrocket_invoice,
)

logger = logging.getLogger(__name__)

WebHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@dataclass(slots=True)
class ProviderWebhookDeps:
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    bot: Bot
    i18n: JsonI18n
    service_channel: ServiceChannel
    metrics: PaymentBotMetrics | None = None


@dataclass(frozen=True, slots=True)
class _ProviderSpec:
    provider: InvoiceProvider
    metric: str
    signature_header: str
    token: str | None
    verify: Callable[[str, bytes, str | None], bool]
    parse: Callable[[Mapping[str, Any]], Any]
    process: Callable[..., Awaitable[PaidInvoiceResult]]
    deliver: Callable[..., Awaitable[Any]]


def make_cryptobot_webhook_handler(deps: ProviderWebhookDeps) -> WebHandler:
    token = deps.settings.cryptobot.token
    spec = _ProviderSpec(
        provider=InvoiceProvider.cryptobot,
        metric="cryptobot",
        signature_header=cryptobot.WEBHOOK_SIGNATURE_HEADER,
        token=token.get_secret_value() if token is not None else None,
        verify=cryptobot.verify_webhook_signature,
        parse=cryptobot.invoice_from_webhook_update,
        process=process_paid_cryptobot_invoice,
        deliver=_cryptobot_deliver,
    )
    return _make_handler(deps, spec)


def make_xrocket_webhook_handler(deps: ProviderWebhookDeps) -> WebHandler:
    token = deps.settings.xrocket.token
    spec = _ProviderSpec(
        provider=InvoiceProvider.xrocket,
        metric="xrocket",
        signature_header=xrocket.WEBHOOK_SIGNATURE_HEADER,
        token=token.get_secret_value() if token is not None else None,
        verify=xrocket.verify_webhook_signature,
        parse=xrocket.invoice_from_webhook,
        process=process_paid_xrocket_invoice,
        deliver=_xrocket_deliver,
    )
    return _make_handler(deps, spec)


def _make_handler(deps: ProviderWebhookDeps, spec: _ProviderSpec) -> WebHandler:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _handle(deps, spec, request)

    return handler


async def _handle(
    deps: ProviderWebhookDeps,
    spec: _ProviderSpec,
    request: web.Request,
) -> web.StreamResponse:
    body = await request.read()
    signature = request.headers.get(spec.signature_header)
    if spec.token is None or not spec.verify(spec.token, body, signature):
        logger.warning("%s webhook rejected: invalid signature", spec.metric)
        return web.Response(status=401)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400)
    if not isinstance(data, dict):
        return web.Response(status=400)

    invoice = spec.parse(data)
    if invoice is None:
        return web.Response(status=200)  # not a paid-invoice event

    now = datetime.now(UTC)
    try:
        result = await _process(deps, spec, invoice, now)
    except Exception:
        # The poller remains a fallback, so a transient failure here does not lose the payment.
        logger.exception(
            "%s webhook processing failed",
            spec.metric,
            extra={"provider_invoice_id": invoice.invoice_id},
        )
        return web.Response(status=500)

    if not result.processed:
        if deps.metrics is not None:
            deps.metrics.record_payment(provider=spec.metric, status="duplicate")
        return web.Response(status=200)

    if deps.metrics is not None:
        deps.metrics.record_payment(provider=spec.metric, status="paid", amount_usd=result.gross)
    await spec.deliver(
        result,
        bot=deps.bot,
        settings=deps.settings,
        i18n=deps.i18n,
        service_channel=deps.service_channel,
        now=now,
    )
    return web.Response(status=200)


async def _process(
    deps: ProviderWebhookDeps,
    spec: _ProviderSpec,
    invoice: Any,
    now: datetime,
) -> PaidInvoiceResult:
    async with session_scope(deps.session_factory) as session:
        await record_provider_event(
            session,
            provider=spec.provider,
            external_id=invoice.invoice_id,
            raw_payload=invoice.raw,
            now=now,
        )
        return await spec.process(
            session,
            provider_invoice=invoice,
            now=now,
            settings=deps.settings,
        )
