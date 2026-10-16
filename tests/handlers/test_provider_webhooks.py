from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from bot.config import Settings
from bot.core.http import HealthDependencies, HealthState, create_http_app
from bot.core.i18n import JsonI18n
from bot.core.metrics import create_metrics_registry
from bot.handlers.provider_webhooks import (
    ProviderWebhookDeps,
    make_cryptobot_webhook_handler,
    make_xrocket_webhook_handler,
)
from bot.models import Invoice, InvoiceProvider, InvoiceStatus, Subscription
from bot.payments import cryptobot, xrocket
from bot.repos.invoices import create_pending_provider_invoice, mark_invoice_provider_created
from bot.repos.tariffs import ensure_configured_tariff
from bot.repos.users import ensure_minimal_user
from bot.services.pricing import quote_tariff

CB_TOKEN = "cryptobot-token"
XR_TOKEN = "xrocket-token"
TG_ID = 4242


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str, **_: Any) -> None:
        self.messages.append((chat_id, text))

    async def create_chat_invite_link(self, **_: Any) -> Any:
        raise AssertionError("no protected chats in this test")


class FakeServiceChannel:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def send_html(self, text: str) -> bool:
        self.events.append(text)
        return True


@asynccontextmanager
async def _client(app: web.Application) -> AsyncIterator[TestClient]:
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def _sign(token: str, body: bytes) -> str:
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


# --- signature + parsing units ------------------------------------------------------------


def test_cryptobot_signature_and_parse() -> None:
    body = b'{"update_type":"invoice_paid"}'
    assert cryptobot.verify_webhook_signature(CB_TOKEN, body, _sign(CB_TOKEN, body)) is True
    assert cryptobot.verify_webhook_signature(CB_TOKEN, body, "bad") is False
    assert cryptobot.verify_webhook_signature(CB_TOKEN, body, None) is False

    invoice = cryptobot.invoice_from_webhook_update(
        {"update_type": "invoice_paid", "payload": {"invoice_id": 5, "status": "paid"}}
    )
    assert invoice is not None
    assert invoice.invoice_id == "5"
    assert cryptobot.invoice_from_webhook_update({"update_type": "other"}) is None


def test_xrocket_signature_and_parse() -> None:
    body = b'{"type":"invoicePay"}'
    assert xrocket.verify_webhook_signature(XR_TOKEN, body, _sign(XR_TOKEN, body)) is True
    assert xrocket.verify_webhook_signature(XR_TOKEN, body, "bad") is False

    invoice = xrocket.invoice_from_webhook(
        {"type": "invoicePay", "data": {"id": 9, "status": "paid", "payload": "inv:z"}}
    )
    assert invoice is not None
    assert invoice.invoice_id == "9"
    assert invoice.payload == "inv:z"
    assert xrocket.invoice_from_webhook({"type": "subscriptionPay"}) is None


# --- handler end-to-end -------------------------------------------------------------------


async def _seed_invoice(
    session_factory: Any,
    settings: Settings,
    *,
    provider: InvoiceProvider,
    provider_invoice_id: str,
    payload: str,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        user = await ensure_minimal_user(session, tg_id=TG_ID, i18n=JsonI18n())
        tariff = await ensure_configured_tariff(session, settings, "1m")
        quote = quote_tariff(settings, "1m", user.status)
        invoice = await create_pending_provider_invoice(
            session,
            provider=provider,
            user=user,
            tariff_id=tariff.id,
            quote=quote,
            currency="USD",
            expires_at=now + timedelta(hours=1),
            payload=payload,
        )
        await mark_invoice_provider_created(
            session, invoice, provider_invoice_id=provider_invoice_id, raw_payload={}
        )
        await session.commit()


def _deps(session_factory: Any, settings: Settings, bot: FakeBot, sc: FakeServiceChannel):
    return ProviderWebhookDeps(
        session_factory=session_factory,
        settings=settings,
        bot=bot,  # type: ignore[arg-type]
        i18n=JsonI18n(),
        service_channel=sc,  # type: ignore[arg-type]
        metrics=None,
    )


def _app_with(path: str, handler: Any) -> web.Application:
    return create_http_app(
        HealthDependencies(check_db=lambda: True, check_redis=lambda: True, check_bot=lambda: True),
        HealthState(),
        registry=create_metrics_registry(),
        webhooks=[(path, handler)],
    )


async def _paid_count(session_factory: Any, provider: InvoiceProvider) -> int:
    async with session_factory() as session:
        rows = await session.scalars(
            select(Invoice).where(
                Invoice.provider == provider, Invoice.status == InvoiceStatus.paid
            )
        )
        return len(list(rows.all()))


async def test_cryptobot_webhook_marks_paid_and_grants(session_factory: Any) -> None:
    settings = Settings(cryptobot={"token": CB_TOKEN})
    await _seed_invoice(
        session_factory,
        settings,
        provider=InvoiceProvider.cryptobot,
        provider_invoice_id="12345",
        payload="inv:cb1",
    )
    bot, sc = FakeBot(), FakeServiceChannel()
    handler = make_cryptobot_webhook_handler(_deps(session_factory, settings, bot, sc))
    body = json.dumps(
        {
            "update_type": "invoice_paid",
            "payload": {"invoice_id": 12345, "status": "paid", "payload": "inv:cb1"},
        }
    ).encode()

    async with _client(_app_with(settings.cryptobot.webhook_path, handler)) as client:
        first = await client.post(
            settings.cryptobot.webhook_path,
            data=body,
            headers={"crypto-pay-api-signature": _sign(CB_TOKEN, body)},
        )
        assert first.status == 200
        assert await _paid_count(session_factory, InvoiceProvider.cryptobot) == 1
        second = await client.post(
            settings.cryptobot.webhook_path,
            data=body,
            headers={"crypto-pay-api-signature": _sign(CB_TOKEN, body)},
        )
        assert second.status == 200  # idempotent

    assert await _paid_count(session_factory, InvoiceProvider.cryptobot) == 1
    async with session_factory() as session:
        sub = await session.scalar(select(Subscription))
        assert sub is not None
    assert sc.events  # service channel notified once


async def test_cryptobot_webhook_rejects_bad_signature(session_factory: Any) -> None:
    settings = Settings(cryptobot={"token": CB_TOKEN})
    handler = make_cryptobot_webhook_handler(
        _deps(session_factory, settings, FakeBot(), FakeServiceChannel())
    )
    async with _client(_app_with(settings.cryptobot.webhook_path, handler)) as client:
        resp = await client.post(
            settings.cryptobot.webhook_path, data=b"{}", headers={"crypto-pay-api-signature": "bad"}
        )
        assert resp.status == 401


async def test_cryptobot_webhook_ignores_non_paid_update(session_factory: Any) -> None:
    settings = Settings(cryptobot={"token": CB_TOKEN})
    handler = make_cryptobot_webhook_handler(
        _deps(session_factory, settings, FakeBot(), FakeServiceChannel())
    )
    body = json.dumps({"update_type": "something_else"}).encode()
    async with _client(_app_with(settings.cryptobot.webhook_path, handler)) as client:
        resp = await client.post(
            settings.cryptobot.webhook_path,
            data=body,
            headers={"crypto-pay-api-signature": _sign(CB_TOKEN, body)},
        )
        assert resp.status == 200
    assert await _paid_count(session_factory, InvoiceProvider.cryptobot) == 0


async def test_xrocket_webhook_marks_paid_and_grants(session_factory: Any) -> None:
    settings = Settings(xrocket={"token": XR_TOKEN, "enabled": True})
    await _seed_invoice(
        session_factory,
        settings,
        provider=InvoiceProvider.xrocket,
        provider_invoice_id="777",
        payload="inv:xr1",
    )
    bot, sc = FakeBot(), FakeServiceChannel()
    handler = make_xrocket_webhook_handler(_deps(session_factory, settings, bot, sc))
    body = json.dumps(
        {"type": "invoicePay", "data": {"id": 777, "status": "paid", "payload": "inv:xr1"}}
    ).encode()

    async with _client(_app_with(settings.xrocket.webhook_path, handler)) as client:
        resp = await client.post(
            settings.xrocket.webhook_path,
            data=body,
            headers={"Rocket-Pay-Signature": _sign(XR_TOKEN, body)},
        )
        assert resp.status == 200

    assert await _paid_count(session_factory, InvoiceProvider.xrocket) == 1
    assert sc.events


async def test_xrocket_webhook_rejects_bad_signature(session_factory: Any) -> None:
    settings = Settings(xrocket={"token": XR_TOKEN, "enabled": True})
    handler = make_xrocket_webhook_handler(
        _deps(session_factory, settings, FakeBot(), FakeServiceChannel())
    )
    async with _client(_app_with(settings.xrocket.webhook_path, handler)) as client:
        resp = await client.post(
            settings.xrocket.webhook_path, data=b"{}", headers={"Rocket-Pay-Signature": "bad"}
        )
        assert resp.status == 401
