"""Widen invoices.telegram_payment_charge_id 128 -> 255.

Telegram Stars charge ids exceed the original VARCHAR(128) (observed ~131 chars), which made
``mark_stars_invoice_paid`` fail with ``StringDataRightTruncationError`` and roll back a real
payment (subscription not extended, access not delivered). Widening the column lets Stars
settlement persist. This is a metadata-only ALTER on Postgres (no table rewrite); prod applies
it via the pre-deploy ``alembic upgrade head``.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260703_0011"
down_revision = "20260701_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "invoices",
        "telegram_payment_charge_id",
        existing_type=sa.String(length=128),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "invoices",
        "telegram_payment_charge_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
