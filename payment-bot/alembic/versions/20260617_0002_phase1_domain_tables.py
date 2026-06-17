from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260617_0002"
down_revision = "20260617_0001"
branch_labels = None
depends_on = None


user_status = sa.Enum(
    "basic",
    "bronze",
    "silver",
    "gold",
    name="user_status",
    native_enum=False,
    create_constraint=True,
)
subscription_source = sa.Enum(
    "manual",
    "cryptobot",
    "stars",
    "xrocket",
    name="subscription_source",
    native_enum=False,
    create_constraint=True,
)
invoice_provider = sa.Enum(
    "cryptobot",
    "stars",
    "xrocket",
    "manual",
    name="invoice_provider",
    native_enum=False,
    create_constraint=True,
)
invoice_status = sa.Enum(
    "active",
    "paid",
    "expired",
    "cancelled",
    name="invoice_status",
    native_enum=False,
    create_constraint=True,
)
promo_kind = sa.Enum(
    "percent",
    "fixed_usd",
    "extra_days",
    name="promo_kind",
    native_enum=False,
    create_constraint=True,
)
provider_event_provider = sa.Enum(
    "cryptobot",
    "stars",
    "xrocket",
    "manual",
    name="provider_event_provider",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("lang_code", sa.String(length=8), server_default="ru", nullable=False),
        sa.Column("status", user_status, server_default="basic", nullable=False),
        sa.Column("total_active_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("referrer_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_users_tg_id", "users", ["tg_id"], unique=True)
    op.create_index("ix_users_referrer_id", "users", ["referrer_id"])

    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=False),
        sa.Column("base_price_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_tariffs_code"),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", invoice_provider, nullable=False),
        sa.Column("provider_invoice_id", sa.String(length=128), nullable=True),
        sa.Column("telegram_payment_charge_id", sa.String(length=128), nullable=True),
        sa.Column("tariff_id", sa.Integer(), nullable=True),
        sa.Column("amount_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("gross", sa.Numeric(12, 2), nullable=False),
        sa.Column("fee", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("net", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("status", invoice_status, server_default="active", nullable=False),
        sa.Column("payload", sa.String(length=128), nullable=False),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tariff_id"], ["tariffs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("payload", name="uq_invoices_payload"),
    )
    op.create_index("ix_invoices_user_id", "invoices", ["user_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_invoices_expires_at", "invoices", ["expires_at"])
    op.create_index(
        "ix_invoices_provider_provider_invoice_id",
        "invoices",
        ["provider", "provider_invoice_id"],
        unique=True,
        postgresql_where=sa.text("provider_invoice_id IS NOT NULL"),
    )
    op.create_index(
        "ix_invoices_telegram_payment_charge_id",
        "invoices",
        ["telegram_payment_charge_id"],
        unique=True,
        postgresql_where=sa.text("telegram_payment_charge_id IS NOT NULL"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_invoice_id", sa.Integer(), nullable=True),
        sa.Column(
            "stars_subscription_active",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("source", subscription_source, server_default="manual", nullable=False),
        sa.Column("notified_3d_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_0d_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_post_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["last_invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=True)
    op.create_index("ix_subscriptions_expires_at", "subscriptions", ["expires_at"])

    op.create_table(
        "promos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("kind", promo_kind, nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("allowed_tariffs", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("code", name="uq_promos_code"),
    )

    op.create_table(
        "promo_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("promo_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column(
            "redeemed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["promo_id"], ["promos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("promo_id", "user_id", name="uq_promo_redemptions_promo_id_user_id"),
    )
    op.create_index("ix_promo_redemptions_promo_id", "promo_redemptions", ["promo_id"])
    op.create_index("ix_promo_redemptions_user_id", "promo_redemptions", ["user_id"])

    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("referrer_id", sa.Integer(), nullable=False),
        sa.Column("referee_id", sa.Integer(), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("custom_percent", sa.Numeric(5, 2), nullable=True),
        sa.ForeignKeyConstraint(["referee_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_referrals_referrer_id", "referrals", ["referrer_id"])
    op.create_index("ix_referrals_referee_id", "referrals", ["referee_id"], unique=True)

    op.create_table(
        "referral_earnings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("referrer_id", sa.Integer(), nullable=False),
        sa.Column("referee_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("paid_out", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("paid_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referee_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_referral_earnings_referrer_id", "referral_earnings", ["referrer_id"])
    op.create_index("ix_referral_earnings_referee_id", "referral_earnings", ["referee_id"])
    op.create_index("ix_referral_earnings_invoice_id", "referral_earnings", ["invoice_id"])

    op.create_table(
        "broadcast_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_user_id", sa.Integer(), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=True),
        sa.Column("target_filter", sa.JSON(), nullable=True),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="SET NULL"),
    )

    op.create_table(
        "provider_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", provider_event_provider, nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_provider_events_provider_external_id",
        "provider_events",
        ["provider", "external_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_events_provider_external_id", table_name="provider_events")
    op.drop_table("provider_events")

    op.drop_table("broadcast_logs")

    op.drop_index("ix_referral_earnings_invoice_id", table_name="referral_earnings")
    op.drop_index("ix_referral_earnings_referee_id", table_name="referral_earnings")
    op.drop_index("ix_referral_earnings_referrer_id", table_name="referral_earnings")
    op.drop_table("referral_earnings")

    op.drop_index("ix_referrals_referee_id", table_name="referrals")
    op.drop_index("ix_referrals_referrer_id", table_name="referrals")
    op.drop_table("referrals")

    op.drop_index("ix_promo_redemptions_user_id", table_name="promo_redemptions")
    op.drop_index("ix_promo_redemptions_promo_id", table_name="promo_redemptions")
    op.drop_table("promo_redemptions")

    op.drop_table("promos")

    op.drop_index("ix_subscriptions_expires_at", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_invoices_telegram_payment_charge_id", table_name="invoices")
    op.drop_index("ix_invoices_provider_provider_invoice_id", table_name="invoices")
    op.drop_index("ix_invoices_expires_at", table_name="invoices")
    op.drop_index("ix_invoices_status", table_name="invoices")
    op.drop_index("ix_invoices_user_id", table_name="invoices")
    op.drop_table("invoices")

    op.drop_table("tariffs")

    op.drop_index("ix_users_referrer_id", table_name="users")
    op.drop_index("ix_users_tg_id", table_name="users")
    op.drop_table("users")
