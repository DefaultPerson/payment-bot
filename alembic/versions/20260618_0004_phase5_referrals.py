from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260618_0004"
down_revision = "20260618_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referral_percent", sa.Numeric(5, 2), nullable=True))
    op.create_unique_constraint(
        "uq_referral_earnings_invoice_id",
        "referral_earnings",
        ["invoice_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_referral_earnings_invoice_id",
        "referral_earnings",
        type_="unique",
    )
    op.drop_column("users", "referral_percent")
