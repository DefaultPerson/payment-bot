from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Tribute signs each webhook with an HMAC-SHA256 of the raw request body, keyed by the
# creator's API key, delivered in the ``trbt-signature`` header.
SIGNATURE_HEADER = "trbt-signature"
DIGITAL_PRODUCT_EVENT = "new_digital_product"


class TributeAPIError(RuntimeError):
    """Raised for Tribute API/webhook errors."""


@dataclass(frozen=True, slots=True)
class TributeDigitalProductEvent:
    """A parsed ``new_digital_product`` (one-time purchase) webhook event.

    ``product_id`` maps to a local tariff (config), ``telegram_user_id`` to our user, and
    ``purchase_id`` / ``transaction_id`` are the provider ids used for idempotency. ``amount``
    is in the currency's minor units (e.g. euro cents).
    """

    product_id: int
    telegram_user_id: int
    purchase_id: int
    transaction_id: int
    amount: int
    currency: str
    telegram_username: str | None
    product_name: str | None
    raw: dict[str, Any]


def verify_signature(api_key: str, body: bytes, signature: str | None) -> bool:
    """Constant-time check of the ``trbt-signature`` header against HMAC-SHA256(body)."""
    if not signature:
        return False
    expected = hmac.new(api_key.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_digital_product_event(data: Mapping[str, Any]) -> TributeDigitalProductEvent | None:
    """Parse a Tribute webhook body; return None unless it is a ``new_digital_product`` event."""
    if data.get("name") != DIGITAL_PRODUCT_EVENT:
        return None
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return None
    try:
        return TributeDigitalProductEvent(
            product_id=int(payload["product_id"]),
            telegram_user_id=int(payload["telegram_user_id"]),
            purchase_id=int(payload["purchase_id"]),
            transaction_id=int(payload["transaction_id"]),
            amount=int(payload.get("amount") or 0),
            currency=str(payload.get("currency") or ""),
            telegram_username=_optional_str(payload.get("telegram_username")),
            product_name=_optional_str(payload.get("product_name")),
            raw=dict(data),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
