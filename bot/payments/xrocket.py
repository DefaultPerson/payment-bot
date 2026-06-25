from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from aiorocket2 import xRocketAPIError as SDKXRocketAPIError
from aiorocket2 import xRocketClient as SDKXRocketClient


class SupportsXRocketSDKClient(Protocol):
    async def create_invoice(self, **kwargs: Any) -> Any: ...

    async def get_invoice(self, invoice_id: int) -> Any: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class XRocketInvoice:
    invoice_id: str
    status: str
    amount: Decimal
    currency: str | None
    payload: str | None
    pay_url: str | None
    created_at: datetime | None
    paid_at: datetime | None
    raw: dict[str, Any]


class XRocketAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        status: int | None = None,
    ) -> None:
        self.payload = payload or {}
        self.status = status
        super().__init__(message)


class XRocketClient:
    def __init__(
        self,
        api_key: str,
        *,
        testnet: bool = False,
        base_url: str | None = None,
        client: SupportsXRocketSDKClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.testnet = testnet
        self.base_url = base_url
        self.client = client
        self._owned_client: SupportsXRocketSDKClient | None = None

    async def close(self) -> None:
        if self._owned_client is None:
            return
        await self._owned_client.aclose()
        self._owned_client = None

    async def create_invoice(
        self,
        *,
        amount: Decimal,
        payload: str,
        description: str,
        currency: str,
        expires_in: int,
    ) -> XRocketInvoice:
        client = self._get_client()
        try:
            invoice = await client.create_invoice(
                amount=float(amount),
                min_payment=0,
                num_payments=1,
                currency=currency,
                description=description,
                payload=payload,
                expired_in=expires_in,
            )
        except SDKXRocketAPIError as exc:
            raise _api_error_from_sdk(exc) from exc
        return _invoice_from_sdk(invoice)

    async def get_invoice(self, invoice_id: str) -> XRocketInvoice:
        client = self._get_client()
        try:
            invoice = await client.get_invoice(int(invoice_id))
        except SDKXRocketAPIError as exc:
            raise _api_error_from_sdk(exc) from exc
        return _invoice_from_sdk(invoice)

    def _get_client(self) -> SupportsXRocketSDKClient:
        if self.client is not None:
            return self.client
        if self._owned_client is None:
            kwargs: dict[str, Any] = {"testnet": self.testnet}
            if self.base_url is not None:
                kwargs["base_url"] = self.base_url
            self._owned_client = SDKXRocketClient(self.api_key, **kwargs)
        return self._owned_client


def _invoice_from_sdk(invoice: Any) -> XRocketInvoice:
    return XRocketInvoice(
        invoice_id=_invoice_id_from_sdk(invoice),
        status=_status_value(invoice.status),
        amount=Decimal(str(invoice.amount)),
        currency=getattr(invoice, "currency", None),
        payload=getattr(invoice, "payload", None),
        pay_url=getattr(invoice, "link", None),
        created_at=_datetime_or_none(getattr(invoice, "created", None)),
        paid_at=_datetime_or_none(getattr(invoice, "paid", None)),
        raw=_raw_invoice(invoice),
    )


def _invoice_id_from_sdk(invoice: Any) -> str:
    invoice_id = getattr(invoice, "id", None)
    if invoice_id is None:
        msg = "xRocket invoice id is missing"
        raise XRocketAPIError(msg)
    invoice_id_text = str(invoice_id)
    try:
        int(invoice_id_text)
    except (TypeError, ValueError) as exc:
        msg = "xRocket invoice id must be numeric"
        raise XRocketAPIError(msg) from exc
    return invoice_id_text


def _status_value(status: object) -> str:
    value = getattr(status, "value", None)
    return str(value) if value is not None else str(status)


def _datetime_or_none(value: object) -> datetime | None:
    if value is None or getattr(value, "raw", None) is None:
        return None
    dt = getattr(value, "datetime", None)
    return dt if isinstance(dt, datetime) else None


def _raw_invoice(invoice: Any) -> dict[str, Any]:
    as_dict = getattr(invoice, "as_dict", None)
    if callable(as_dict):
        raw = as_dict()
        if isinstance(raw, dict):
            return raw
    return dict(invoice)


def _api_error_from_sdk(exc: SDKXRocketAPIError) -> XRocketAPIError:
    payload = dict(getattr(exc, "payload", {}) or {})
    status = getattr(exc, "status", None)
    message = str(getattr(exc, "message", None) or exc)
    return XRocketAPIError(message, payload=payload, status=status)
