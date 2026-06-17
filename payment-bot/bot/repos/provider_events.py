from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import InvoiceProvider, ProviderEvent


async def record_provider_event(
    session: AsyncSession,
    *,
    provider: InvoiceProvider,
    external_id: str,
    raw_payload: dict[str, Any],
    now: datetime,
) -> ProviderEvent:
    event = ProviderEvent(
        provider=provider,
        external_id=external_id,
        raw_payload=raw_payload,
        received_at=now,
        processed_at=None,
    )
    session.add(event)
    await session.flush()
    return event
