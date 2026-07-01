from __future__ import annotations

from alembic import op

revision = "20260701_0007"
down_revision = "20260626_0006"
branch_labels = None
depends_on = None


# (table, check-constraint name = enum name, column) for the string-enum CHECK constraints
# that gate provider/source values. These are non-native enums (VARCHAR + CHECK), so adding
# the "tribute" value means widening each CHECK constraint.
_CONSTRAINTS = (
    ("invoices", "invoice_provider", "provider"),
    ("subscriptions", "subscription_source", "source"),
    ("provider_events", "provider_event_provider", "provider"),
)
_WITHOUT_TRIBUTE = ("cryptobot", "stars", "xrocket", "manual")
_WITH_TRIBUTE = (*_WITHOUT_TRIBUTE, "tribute")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    for table, name, column in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _in_clause(column, _WITH_TRIBUTE))


def downgrade() -> None:
    for table, name, column in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _in_clause(column, _WITHOUT_TRIBUTE))
