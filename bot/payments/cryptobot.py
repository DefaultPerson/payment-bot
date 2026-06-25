from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from aiohttp import ClientSession


class SupportsPost(Protocol):
    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class CryptoBotInvoice:
    invoice_id: str
    status: str
    payload: str | None
    pay_url: str | None
    raw: dict[str, Any]


class CryptoBotAPIError(RuntimeError):
    pass


class CryptoBotClient:
    def __init__(
        self,
        token: str,
        api_url: str,
        session: SupportsPost | None = None,
    ) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.session = session
        self._owned_session: ClientSession | None = None

    async def close(self) -> None:
        if self._owned_session is None:
            return
        await self._owned_session.close()
        self._owned_session = None

    async def create_invoice(
        self,
        *,
        amount: Decimal,
        payload: str,
        description: str,
        paid_btn_url: str,
        accepted_assets: list[str],
        expires_in: int,
    ) -> CryptoBotInvoice:
        result = await self._post(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "USD",
                "amount": f"{amount:.2f}",
                "accepted_assets": ",".join(accepted_assets),
                "description": description,
                "payload": payload,
                "paid_btn_name": "openBot",
                "paid_btn_url": paid_btn_url,
                "expires_in": expires_in,
            },
        )
        return _invoice_from_payload(result)

    async def get_invoices(
        self,
        invoice_ids: list[str],
        status: str | None = None,
    ) -> list[CryptoBotInvoice]:
        body: dict[str, Any] = {"invoice_ids": ",".join(invoice_ids)}
        if status is not None:
            body["status"] = status

        result = await self._post("getInvoices", body)
        items = result.get("items", result) if isinstance(result, dict) else result
        return [_invoice_from_payload(item) for item in items]

    async def _post(self, method: str, body: dict[str, Any]) -> Any:
        session = await self._get_session()
        async with session.post(
            f"{self.api_url}/api/{method}",
            headers={"Crypto-Pay-API-Token": self.token},
            json=body,
        ) as response:
            payload = await response.json()

        if not payload.get("ok"):
            msg = str(payload.get("error") or payload)
            raise CryptoBotAPIError(msg)
        return payload["result"]

    async def _get_session(self) -> SupportsPost:
        if self.session is not None:
            return self.session
        if self._owned_session is None:
            self._owned_session = ClientSession()
        return self._owned_session


def _invoice_from_payload(payload: dict[str, Any]) -> CryptoBotInvoice:
    return CryptoBotInvoice(
        invoice_id=str(payload["invoice_id"]),
        status=str(payload.get("status") or ""),
        payload=payload.get("payload"),
        pay_url=payload.get("bot_invoice_url") or payload.get("pay_url"),
        raw=payload,
    )
