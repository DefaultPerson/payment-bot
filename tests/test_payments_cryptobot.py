from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bot.payments import cryptobot as cryptobot_module
from bot.payments.cryptobot import CryptoBotAPIError, CryptoBotClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or []
        self.closed = False

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.responses:
            return FakeResponse(self.responses.pop(0))
        if url.endswith("/createInvoice"):
            return FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "invoice_id": 123,
                        "status": "active",
                        "bot_invoice_url": "https://t.me/CryptoBot?start=pay",
                        "payload": json["payload"],
                    },
                },
            )
        return FakeResponse({"ok": True, "result": []})

    async def close(self) -> None:
        self.closed = True


async def test_create_invoice_posts_fiat_invoice_shape() -> None:
    session = FakeSession()
    client = CryptoBotClient(
        token="secret",
        api_url="https://pay.crypt.bot/",
        session=session,
    )

    invoice = await client.create_invoice(
        amount=Decimal("73.30"),
        payload="inv:abc",
        description="Hydra 1m",
        paid_btn_url="https://t.me/HydraBot",
        accepted_assets=["USDT", "TON"],
        expires_in=3600,
    )

    assert invoice.invoice_id == "123"
    assert invoice.status == "active"
    assert invoice.payload == "inv:abc"
    assert invoice.pay_url == "https://t.me/CryptoBot?start=pay"
    assert invoice.raw["invoice_id"] == 123
    assert session.calls[0]["url"] == "https://pay.crypt.bot/api/createInvoice"
    assert session.calls[0]["headers"] == {"Crypto-Pay-API-Token": "secret"}
    assert session.calls[0]["json"] == {
        "currency_type": "fiat",
        "fiat": "USD",
        "amount": "73.30",
        "accepted_assets": "USDT,TON",
        "description": "Hydra 1m",
        "payload": "inv:abc",
        "paid_btn_name": "openBot",
        "paid_btn_url": "https://t.me/HydraBot",
        "expires_in": 3600,
    }


async def test_get_invoices_posts_comma_separated_ids_and_status() -> None:
    session = FakeSession(
        responses=[
            {
                "ok": True,
                "result": [
                    {
                        "invoice_id": "101",
                        "status": "paid",
                        "payload": "inv:101",
                        "pay_url": "https://pay.crypt.bot/old/101",
                    },
                    {
                        "invoice_id": 102,
                        "status": "active",
                        "payload": "inv:102",
                        "bot_invoice_url": "https://t.me/CryptoBot?start=102",
                        "pay_url": "https://pay.crypt.bot/old/102",
                    },
                ],
            },
        ],
    )
    client = CryptoBotClient(token="secret", api_url="https://pay.crypt.bot", session=session)

    invoices = await client.get_invoices(["101", "102"], status="paid")

    assert [invoice.invoice_id for invoice in invoices] == ["101", "102"]
    assert [invoice.pay_url for invoice in invoices] == [
        "https://pay.crypt.bot/old/101",
        "https://t.me/CryptoBot?start=102",
    ]
    assert session.calls[0]["url"] == "https://pay.crypt.bot/api/getInvoices"
    assert session.calls[0]["json"] == {"invoice_ids": "101,102", "status": "paid"}


async def test_api_error_is_raised_when_response_is_not_ok() -> None:
    session = FakeSession(
        responses=[
            {
                "ok": False,
                "error": {"code": 401, "name": "TOKEN_INVALID"},
            },
        ],
    )
    client = CryptoBotClient(token="bad", api_url="https://pay.crypt.bot", session=session)

    with pytest.raises(CryptoBotAPIError, match="TOKEN_INVALID"):
        await client.get_invoices(["123"])


async def test_close_only_closes_owned_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    injected = FakeSession()
    injected_client = CryptoBotClient(
        token="secret",
        api_url="https://pay.crypt.bot",
        session=injected,
    )

    await injected_client.close()

    assert injected.closed is False

    owned = FakeSession(responses=[{"ok": True, "result": []}])
    monkeypatch.setattr(cryptobot_module, "ClientSession", lambda: owned)
    owned_client = CryptoBotClient(token="secret", api_url="https://pay.crypt.bot")

    await owned_client.get_invoices(["123"])
    await owned_client.close()

    assert owned.closed is True
