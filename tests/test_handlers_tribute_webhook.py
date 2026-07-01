from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from bot.config import Settings
from bot.core.http import HealthDependencies, HealthState, create_http_app
from bot.core.i18n import JsonI18n
from bot.core.metrics import create_metrics_registry
from bot.handlers.tribute_webhook import TributeWebhookDeps, create_tribute_webhook_handler
from bot.models import Invoice, InvoiceProvider, InvoiceStatus, Subscription, User
from bot.payments.tribute import DIGITAL_PRODUCT_EVENT

API_KEY = "webhook-secret-key"
PATH = "/webhooks/tribute"
PRODUCT_ID = 999
TG_ID = 555


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str, **_: Any) -> None:
        self.messages.append((chat_id, text))

    async def create_chat_invite_link(self, **_: Any) -> Any:
        raise AssertionError("no protected chats are configured in this test")


class FakeServiceChannel:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.events.append(text)
        return True


def _settings() -> Settings:
    return Settings(
        tribute={
            "enabled": True,
            "token": API_KEY,
            "products": [
                {"product_id": PRODUCT_ID, "tariff_code": "1m", "payment_link": "https://t.me/a"},
            ],
        },
    )


def _build_app(
    session_factory: Any, bot: FakeBot, service_channel: FakeServiceChannel
) -> web.Application:
    settings = _settings()
    deps = TributeWebhookDeps(
        session_factory=session_factory,
        settings=settings,
        bot=bot,  # type: ignore[arg-type]
        i18n=JsonI18n(),
        service_channel=service_channel,  # type: ignore[arg-type]
        metrics=None,
    )
    return create_http_app(
        HealthDependencies(check_db=lambda: True, check_redis=lambda: True, check_bot=lambda: True),
        HealthState(),
        registry=create_metrics_registry(),
        tribute_webhook=(PATH, create_tribute_webhook_handler(deps)),
    )


@asynccontextmanager
async def _client(app: web.Application) -> AsyncIterator[TestClient]:
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def _event_body(
    *,
    purchase_id: int = 7777,
    transaction_id: int = 8888,
    name: str = DIGITAL_PRODUCT_EVENT,
    product_id: int = PRODUCT_ID,
) -> bytes:
    return json.dumps(
        {
            "name": name,
            "payload": {
                "product_id": product_id,
                "amount": 100,
                "currency": "EUR",
                "telegram_user_id": TG_ID,
                "telegram_username": "buyer",
                "purchase_id": purchase_id,
                "transaction_id": transaction_id,
            },
        }
    ).encode()


def _sign(body: bytes, key: str = API_KEY) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


async def _tribute_invoices(session_factory: Any) -> list[Invoice]:
    async with session_factory() as session:
        result = await session.scalars(
            select(Invoice).where(Invoice.provider == InvoiceProvider.tribute)
        )
        return list(result.all())


async def test_valid_webhook_records_payment_and_grants_access(session_factory: Any) -> None:
    bot = FakeBot()
    service_channel = FakeServiceChannel()
    body = _event_body()
    async with _client(_build_app(session_factory, bot, service_channel)) as client:
        response = await client.post(PATH, data=body, headers={"trbt-signature": _sign(body)})
        assert response.status == 200

    invoices = await _tribute_invoices(session_factory)
    assert len(invoices) == 1
    assert invoices[0].status is InvoiceStatus.paid
    assert invoices[0].provider_invoice_id == "7777"

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.tg_id == TG_ID))
        assert user is not None
        subscription = await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        assert subscription is not None

    assert bot.messages, "access delivery should have been attempted"
    assert service_channel.events, "service channel should have been notified"


async def test_invalid_signature_is_rejected(session_factory: Any) -> None:
    bot = FakeBot()
    body = _event_body()
    async with _client(_build_app(session_factory, bot, FakeServiceChannel())) as client:
        response = await client.post(PATH, data=body, headers={"trbt-signature": "bad"})
        assert response.status == 401

    assert await _tribute_invoices(session_factory) == []


async def test_non_purchase_event_is_ignored(session_factory: Any) -> None:
    body = _event_body(name="new_donation")
    async with _client(_build_app(session_factory, FakeBot(), FakeServiceChannel())) as client:
        response = await client.post(PATH, data=body, headers={"trbt-signature": _sign(body)})
        assert response.status == 200

    assert await _tribute_invoices(session_factory) == []


async def test_unmapped_product_is_ignored(session_factory: Any) -> None:
    body = _event_body(product_id=12345)
    async with _client(_build_app(session_factory, FakeBot(), FakeServiceChannel())) as client:
        response = await client.post(PATH, data=body, headers={"trbt-signature": _sign(body)})
        assert response.status == 200

    assert await _tribute_invoices(session_factory) == []


async def test_duplicate_webhook_is_idempotent(session_factory: Any) -> None:
    bot = FakeBot()
    body = _event_body()
    app = _build_app(session_factory, bot, FakeServiceChannel())
    async with _client(app) as client:
        first = await client.post(PATH, data=body, headers={"trbt-signature": _sign(body)})
        assert first.status == 200
        invoices = await _tribute_invoices(session_factory)
        assert len(invoices) == 1
        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.tg_id == TG_ID))
            subscription = await session.scalar(
                select(Subscription).where(Subscription.user_id == user.id)
            )
            expires_after_first = subscription.expires_at

        second = await client.post(PATH, data=body, headers={"trbt-signature": _sign(body)})
        assert second.status == 200

    invoices = await _tribute_invoices(session_factory)
    assert len(invoices) == 1  # no second invoice
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.tg_id == TG_ID))
        subscription = await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        assert subscription.expires_at == expires_after_first  # not extended twice
