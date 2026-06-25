from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.core.db import session_scope
from bot.core.metrics import PaymentBotMetrics
from bot.repos.invoices import mark_expired_active_invoices
from bot.repos.subscriptions import count_active_subscriptions

logger = logging.getLogger(__name__)


async def invoice_cleanup_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    async with session_scope(session_factory) as session:
        return await mark_expired_active_invoices(session, now=now)


async def metrics_snapshot_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    metrics: PaymentBotMetrics,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    async with session_scope(session_factory) as session:
        active = await count_active_subscriptions(session, now=now)
    metrics.set_subscriptions_active(active)
    return active


async def invoice_cleanup_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    sleep: Callable[[int], Awaitable[None]] = asyncio.sleep,
    max_iterations: int | None = None,
) -> None:
    iterations = 0
    while True:
        try:
            count = await invoice_cleanup_once(session_factory=session_factory)
            if count:
                logger.info("expired active invoices", extra={"count": count})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("invoice cleanup iteration failed")

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        await sleep(settings.observability.invoice_cleanup_interval_seconds)


async def metrics_snapshot_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    metrics: PaymentBotMetrics,
    sleep: Callable[[int], Awaitable[None]] = asyncio.sleep,
    max_iterations: int | None = None,
) -> None:
    iterations = 0
    while True:
        try:
            await metrics_snapshot_once(session_factory=session_factory, metrics=metrics)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("metrics snapshot iteration failed")

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        await sleep(settings.observability.metrics_snapshot_interval_seconds)
