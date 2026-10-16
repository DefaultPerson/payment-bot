from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest


@dataclass(slots=True)
class FakeStatus:
    value: str


@dataclass(slots=True)
class FakeDateTime:
    raw: object | None
    datetime: datetime


class FakeSDKInvoice:
    def __init__(
        self,
        *,
        invoice_id: int,
        status: str,
        payload: str | None = "inv:abc",
        link: str | None = "https://t.me/xrocket?start=pay",
        paid_raw: object | None = None,
    ) -> None:
        self.id = invoice_id
        self.amount = 73.3
        self.min_payment = 0
        self.total_activations = 1
        self.activations_left = 1
        self.description = "Hydra 1m"
        self.hidden_message = ""
        self.payload = payload
        self.callback_url = ""
        self.comments_enabled = False
        self.currency = "USDT"
        self.created = FakeDateTime(raw="2026-06-18T12:00:00Z", datetime=NOW)
        self.paid = FakeDateTime(raw=paid_raw, datetime=NOW)
        self.status = FakeStatus(status)
        self.expired_in = 3600
        self.link = link

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "amount": self.amount,
            "currency": self.currency,
            "payload": self.payload,
            "status": self.status.value,
            "link": self.link,
        }


class FakeSDKClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.invoice = FakeSDKInvoice(invoice_id=123, status="active")

    async def create_invoice(self, **kwargs: Any) -> FakeSDKInvoice:
        self.calls.append(("create_invoice", kwargs))
        return self.invoice

    async def get_invoice(self, invoice_id: int) -> FakeSDKInvoice:
        self.calls.append(("get_invoice", {"invoice_id": invoice_id}))
        return self.invoice

    async def aclose(self) -> None:
        self.closed = True


NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


async def test_create_invoice_maps_decimal_amount_and_payload_to_sdk() -> None:
    from bot.payments.xrocket import XRocketClient

    sdk = FakeSDKClient()
    client = XRocketClient(api_key="secret", client=sdk)

    invoice = await client.create_invoice(
        amount=Decimal("73.30"),
        payload="inv:abc",
        description="Hydra 1m",
        currency="USDT",
        expires_in=3600,
    )

    assert invoice.invoice_id == "123"
    assert invoice.status == "active"
    assert invoice.amount == Decimal("73.3")
    assert invoice.currency == "USDT"
    assert invoice.payload == "inv:abc"
    assert invoice.pay_url == "https://t.me/xrocket?start=pay"
    assert invoice.created_at == NOW
    assert invoice.paid_at is None
    assert invoice.raw["id"] == 123
    assert sdk.calls == [
        (
            "create_invoice",
            {
                "amount": 73.3,
                "min_payment": None,
                "num_payments": 1,
                "currency": "USDT",
                "description": "Hydra 1m",
                "payload": "inv:abc",
                "expired_in": 3600,
            },
        )
    ]


async def test_get_invoice_converts_provider_id_to_int_and_preserves_paid_time() -> None:
    from bot.payments.xrocket import XRocketClient

    sdk = FakeSDKClient()
    sdk.invoice = FakeSDKInvoice(invoice_id=321, status="paid", paid_raw="2026-06-18T12:00:00Z")
    client = XRocketClient(api_key="secret", client=sdk)

    invoice = await client.get_invoice("321")

    assert invoice.invoice_id == "321"
    assert invoice.status == "paid"
    assert invoice.paid_at == NOW
    assert sdk.calls == [("get_invoice", {"invoice_id": 321})]


async def test_close_only_closes_owned_sdk_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.payments import xrocket as xrocket_module
    from bot.payments.xrocket import XRocketClient

    injected = FakeSDKClient()
    injected_client = XRocketClient(api_key="secret", client=injected)

    await injected_client.close()

    assert injected.closed is False

    owned = FakeSDKClient()

    def fake_sdk_client(*args: Any, **kwargs: Any) -> FakeSDKClient:
        owned.calls.append(("init", {"args": args, "kwargs": kwargs}))
        return owned

    monkeypatch.setattr(xrocket_module, "SDKXRocketClient", fake_sdk_client)
    owned_client = XRocketClient(
        api_key="secret",
        testnet=True,
        base_url="https://pay.xrocket.test",
    )

    await owned_client.get_invoice("123")
    await owned_client.close()

    assert owned.closed is True
    assert owned.calls[0] == (
        "init",
        {
            "args": ("secret",),
            "kwargs": {"testnet": True, "base_url": "https://pay.xrocket.test"},
        },
    )


async def test_sdk_errors_are_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.payments import xrocket as xrocket_module
    from bot.payments.xrocket import XRocketAPIError, XRocketClient

    class FakeProviderError(Exception):
        def __init__(self) -> None:
            self.payload = {"message": "TOKEN_INVALID"}
            self.status = 401
            super().__init__("TOKEN_INVALID")

    class FailingSDKClient:
        async def get_invoice(self, invoice_id: int) -> object:
            raise FakeProviderError

    monkeypatch.setattr(xrocket_module, "SDKXRocketAPIError", FakeProviderError)
    client = XRocketClient(api_key="bad", client=FailingSDKClient())

    with pytest.raises(XRocketAPIError, match="TOKEN_INVALID"):
        await client.get_invoice("123")


async def test_provider_invoice_id_must_be_numeric() -> None:
    from bot.payments.xrocket import XRocketAPIError, XRocketClient

    sdk = FakeSDKClient()
    sdk.invoice = FakeSDKInvoice(invoice_id=123, status="active")
    sdk.invoice.id = None
    client = XRocketClient(api_key="secret", client=sdk)

    with pytest.raises(XRocketAPIError, match="invoice id"):
        await client.create_invoice(
            amount=Decimal("73.30"),
            payload="inv:abc",
            description="Hydra 1m",
            currency="USDT",
            expires_in=3600,
        )
