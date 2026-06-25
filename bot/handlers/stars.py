from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.i18n import JsonI18n
from bot.core.logger import ServiceChannel
from bot.core.metrics import PaymentBotMetrics
from bot.core.service_events import fmt_amount, fmt_dt, money, render_event, user_mention
from bot.repos.invoices import lock_invoice_by_id
from bot.services.access import send_access_links
from bot.services.payments import (
    PaidInvoiceResult,
    StarsSuccessfulPayment,
    process_paid_stars_invoice,
    validate_stars_pre_checkout,
)

logger = logging.getLogger(__name__)


async def pre_checkout_query(
    query: PreCheckoutQuery,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del i18n
    try:
        async with session_scope(session_factory) as session:
            result = await validate_stars_pre_checkout(
                session,
                payload=query.invoice_payload,
                currency=query.currency,
                total_amount=query.total_amount,
                now=datetime.now(UTC),
            )
    except SQLAlchemyError:
        logger.exception("failed to validate Stars pre-checkout query")
        await query.answer(ok=False, error_message="payment validation failed")
        return

    await query.answer(ok=result.ok, error_message=result.error_message)


async def successful_payment(
    message: Message,
    settings: Settings,
    i18n: JsonI18n,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    service_channel: ServiceChannel,
    metrics: PaymentBotMetrics | None = None,
) -> None:
    payment = _successful_payment_from_message(message)
    try:
        async with session_scope(session_factory) as session:
            result = await process_paid_stars_invoice(
                session,
                successful_payment=payment,
                now=datetime.now(UTC),
                settings=settings,
            )
    except SQLAlchemyError:
        logger.exception("failed to process Stars successful payment")
        if metrics is not None:
            metrics.record_payment(provider="stars", status="error")
        await message.answer(i18n.gettext("error.generic", _message_language(message, i18n)))
        return
    except Exception:
        logger.exception("rejected Stars successful payment")
        if metrics is not None:
            metrics.record_payment(provider="stars", status="error")
        await message.answer(i18n.gettext("error.generic", _message_language(message, i18n)))
        return

    if metrics is not None:
        if result.processed:
            metrics.record_payment(provider="stars", status="paid", amount_usd=result.gross)
        else:
            metrics.record_payment(provider="stars", status="duplicate")

    if _access_delivered(result):
        return

    if result.processed:
        await _notify_service_channel(service_channel, result)

    delivered_at = datetime.now(UTC)
    if not await _send_access(result, bot=bot, settings=settings, i18n=i18n, now=delivered_at):
        await message.answer(i18n.gettext("error.generic", result.lang_code))
        return

    await _mark_access_delivered(
        session_factory=session_factory,
        invoice_id=result.invoice_id,
        now=delivered_at,
    )
    await message.answer(i18n.gettext("payment.success.text", result.lang_code))


def _successful_payment_from_message(message: Message) -> StarsSuccessfulPayment:
    payment = message.successful_payment
    raw = _model_dump(payment)
    return StarsSuccessfulPayment(
        invoice_payload=payment.invoice_payload,
        currency=payment.currency,
        total_amount=payment.total_amount,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
        is_recurring=getattr(payment, "is_recurring", None),
        is_first_recurring=getattr(payment, "is_first_recurring", None),
        subscription_expiration_date=getattr(payment, "subscription_expiration_date", None),
        raw=raw,
    )


def _model_dump(value: object) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="python")
    return dict(getattr(value, "__dict__", {}))


async def _notify_service_channel(
    service_channel: ServiceChannel,
    result: PaidInvoiceResult,
) -> None:
    try:
        await service_channel.send_html(_payment_event_text(result))
    except Exception:
        logger.exception(
            "failed to send Stars payment event",
            extra={"invoice_id": result.invoice_id},
        )


async def _send_access(
    result: PaidInvoiceResult,
    *,
    bot: Bot,
    settings: Settings,
    i18n: JsonI18n,
    now: datetime,
) -> bool:
    if result.user_tg_id is None or result.lang_code is None:
        logger.error(
            "paid Stars invoice result is missing user delivery data",
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
    except Exception:
        logger.exception(
            "failed to send Stars access links",
            extra={"invoice_id": result.invoice_id, "user_tg_id": result.user_tg_id},
        )
        return False
    return True


async def _mark_access_delivered(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    invoice_id: int | None,
    now: datetime,
) -> None:
    if invoice_id is None:
        return
    try:
        async with session_scope(session_factory) as session:
            invoice = await lock_invoice_by_id(session, invoice_id)
            if invoice is None:
                return
            provider_payload = dict(invoice.provider_payload or {})
            provider_payload["access_delivered_at"] = now.isoformat()
            invoice.provider_payload = provider_payload
    except Exception:
        logger.exception(
            "failed to mark Stars access delivered",
            extra={"invoice_id": invoice_id},
        )


def _access_delivered(result: PaidInvoiceResult) -> bool:
    provider_payload = result.invoice.provider_payload or {}
    return bool(provider_payload.get("access_delivered_at"))


def _payment_event_text(result: PaidInvoiceResult) -> str:
    currency = result.currency or "XTR"
    return render_event(
        glyph="star",
        title="Telegram stars payment paid",
        fields=[
            ("User", user_mention(result.user_tg_id, result.username)),
            ("Payment", money(result.gross, currency)),
        ],
        extra={
            "tariff": result.tariff_code,
            "gross": f"{fmt_amount(result.gross)} {currency}",
            "fee": f"{fmt_amount(result.fee)} {currency}",
            "net": f"{fmt_amount(result.net)} {currency}",
            "expires_at": fmt_dt(result.subscription_expires_at),
        },
    )


def _message_language(message: Message, i18n: JsonI18n) -> str:
    user = message.from_user
    return i18n.resolve_language(user.language_code if user else None)


def create_stars_router() -> Router:
    router = Router(name="stars")
    router.pre_checkout_query()(pre_checkout_query)
    router.message(F.successful_payment)(successful_payment)
    return router
