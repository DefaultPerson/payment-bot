from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260626_0005"
down_revision = "20260618_0004"
branch_labels = None
depends_on = None

# Default plan code that existing subscriptions are backfilled with. Matches the single
# configured plan in bot.config.TariffsConfig (PlanConfig(code="main", ...)).
_DEFAULT_PLAN_CODE = "main"


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("plan_code", sa.String(50), nullable=True))
    # Bind every existing subscription to the single configured plan so tariff→chats
    # enforcement treats current subscribers as members of the default plan.
    op.execute(
        f"UPDATE subscriptions SET plan_code = '{_DEFAULT_PLAN_CODE}' WHERE plan_code IS NULL"
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "plan_code")
