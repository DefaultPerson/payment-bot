from __future__ import annotations

from bot.models import Invoice


def test_telegram_payment_charge_id_column_holds_stars_charge_ids() -> None:
    # Telegram Stars charge ids exceed 128 chars (observed ~131).
    column = Invoice.__table__.c.telegram_payment_charge_id
    assert column.type.length == 255
