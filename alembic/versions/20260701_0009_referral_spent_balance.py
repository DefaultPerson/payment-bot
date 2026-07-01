from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Chained after the free-trial migration (0008) so the two parallel branches form a single
# linear history (0007 -> 0008 trial -> 0009 this) with no alembic multiple-heads.
revision = "20260701_0009"
down_revision = "20260701_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "referral_spent_usd",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "referral_spent_usd")
