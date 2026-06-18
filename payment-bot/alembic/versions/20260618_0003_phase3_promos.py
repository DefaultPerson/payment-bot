from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260618_0003"
down_revision = "20260617_0002"
branch_labels = None
depends_on = None

promo_kind = sa.Enum(
    "percent",
    "fixed_usd",
    "extra_days",
    name="promo_kind",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX ix_promos_code_upper ON promos (upper(code))")

    op.add_column("users", sa.Column("pending_promo_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_pending_promo_id_promos",
        "users",
        "promos",
        ["pending_promo_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_pending_promo_id", "users", ["pending_promo_id"])

    op.add_column("invoices", sa.Column("promo_id", sa.Integer(), nullable=True))
    op.add_column("invoices", sa.Column("promo_code", sa.String(length=64), nullable=True))
    op.add_column("invoices", sa.Column("promo_kind", promo_kind, nullable=True))
    op.add_column("invoices", sa.Column("promo_value", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "invoices",
        sa.Column("promo_discount_usd", sa.Numeric(12, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "invoices",
        sa.Column("promo_extra_days", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_foreign_key(
        "fk_invoices_promo_id_promos",
        "invoices",
        "promos",
        ["promo_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_invoices_promo_id_promos", "invoices", type_="foreignkey")
    op.drop_column("invoices", "promo_extra_days")
    op.drop_column("invoices", "promo_discount_usd")
    op.drop_column("invoices", "promo_value")
    op.drop_column("invoices", "promo_kind")
    op.drop_column("invoices", "promo_code")
    op.drop_column("invoices", "promo_id")

    op.drop_index("ix_users_pending_promo_id", table_name="users")
    op.drop_constraint("fk_users_pending_promo_id_promos", "users", type_="foreignkey")
    op.drop_column("users", "pending_promo_id")
    op.execute("DROP INDEX IF EXISTS ix_promos_code_upper")
