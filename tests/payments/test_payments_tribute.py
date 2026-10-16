from __future__ import annotations

import hashlib
import hmac

from bot.payments.tribute import (
    DIGITAL_PRODUCT_EVENT,
    parse_digital_product_event,
    verify_signature,
)

API_KEY = "super-secret-key"


def _sign(body: bytes) -> str:
    return hmac.new(API_KEY.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_accepts_valid_hmac() -> None:
    body = b'{"name":"new_digital_product"}'
    assert verify_signature(API_KEY, body, _sign(body)) is True


def test_verify_signature_rejects_tampered_body() -> None:
    body = b'{"name":"new_digital_product"}'
    signature = _sign(body)
    assert verify_signature(API_KEY, b'{"name":"tampered"}', signature) is False


def test_verify_signature_rejects_missing_or_wrong_signature() -> None:
    body = b"{}"
    assert verify_signature(API_KEY, body, None) is False
    assert verify_signature(API_KEY, body, "") is False
    assert verify_signature(API_KEY, body, "deadbeef") is False


def _event_body() -> dict:
    return {
        "name": DIGITAL_PRODUCT_EVENT,
        "created_at": "2026-07-01T10:00:00Z",
        "sent_at": "2026-07-01T10:00:01Z",
        "payload": {
            "product_id": 999,
            "product_name": "Hydra 1m",
            "amount": 100,
            "currency": "EUR",
            "user_id": 1,
            "trb_user_id": "trb-abc",
            "telegram_user_id": 555,
            "telegram_username": "buyer",
            "purchase_id": 7777,
            "transaction_id": 8888,
            "purchase_created_at": "2026-07-01T10:00:00Z",
        },
    }


def test_parse_digital_product_event_extracts_fields() -> None:
    event = parse_digital_product_event(_event_body())

    assert event is not None
    assert event.product_id == 999
    assert event.telegram_user_id == 555
    assert event.purchase_id == 7777
    assert event.transaction_id == 8888
    assert event.amount == 100
    assert event.currency == "EUR"
    assert event.telegram_username == "buyer"
    assert event.product_name == "Hydra 1m"


def test_parse_digital_product_event_ignores_other_events() -> None:
    assert parse_digital_product_event({"name": "new_donation", "payload": {}}) is None
    assert parse_digital_product_event({"name": DIGITAL_PRODUCT_EVENT}) is None


def test_parse_digital_product_event_rejects_incomplete_payload() -> None:
    body = _event_body()
    del body["payload"]["purchase_id"]
    assert parse_digital_product_event(body) is None
