from __future__ import annotations

from bot.models import Invoice


def test_telegram_payment_charge_id_column_holds_stars_charge_ids() -> None:
    # Telegram Stars charge ids exceed 128 chars (observed ~131); the column was widened to 255
    # (migration 0011) so mark_stars_invoice_paid no longer truncates and rolls back a real
    # payment. Locks the model side of that fix.
    column = Invoice.__table__.c.telegram_payment_charge_id
    assert column.type.length == 255
