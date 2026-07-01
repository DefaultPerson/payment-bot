from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260701_0008"
down_revision = "20260701_0007"
branch_labels = None
depends_on = None


# subscriptions.source is a non-native enum (VARCHAR + CHECK), so adding the "trial" value
# means widening its CHECK constraint. Only this one enum gains "trial" (a trial mints no
# invoice / provider_event), so invoice_provider / provider_event_provider stay untouched.
_TABLE = "subscriptions"
_CONSTRAINT = "subscription_source"
_COLUMN = "source"
_WITHOUT_TRIAL = ("cryptobot", "stars", "xrocket", "manual", "tribute")
_WITH_TRIAL = (*_WITHOUT_TRIAL, "trial")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("trial_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_clause(_COLUMN, _WITH_TRIAL))


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_clause(_COLUMN, _WITHOUT_TRIAL))
    op.drop_column("users", "trial_used_at")
