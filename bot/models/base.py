from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserStatus(str, enum.Enum):  # noqa: UP042
    basic = "basic"
    bronze = "bronze"
    silver = "silver"
    gold = "gold"


class SubscriptionSource(str, enum.Enum):  # noqa: UP042
    manual = "manual"
    cryptobot = "cryptobot"
    stars = "stars"
    xrocket = "xrocket"


class InvoiceProvider(str, enum.Enum):  # noqa: UP042
    cryptobot = "cryptobot"
    stars = "stars"
    xrocket = "xrocket"
    manual = "manual"


class InvoiceStatus(str, enum.Enum):  # noqa: UP042
    active = "active"
    paid = "paid"
    expired = "expired"
    cancelled = "cancelled"


class PromoKind(str, enum.Enum):  # noqa: UP042
    percent = "percent"
    fixed_usd = "fixed_usd"
    extra_days = "extra_days"


def enum_column(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class SchemaVersionMarker(Base):
    __tablename__ = "schema_version_marker"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(255))
    lang_code: Mapped[str] = mapped_column(String(8), nullable=False, server_default="ru")
    status: Mapped[UserStatus] = mapped_column(
        enum_column(UserStatus, "user_status"),
        nullable=False,
        server_default=UserStatus.basic.value,
    )
    total_active_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    referrer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    referral_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    pending_promo_id: Mapped[int | None] = mapped_column(
        ForeignKey("promos.id", ondelete="SET NULL"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (UniqueConstraint("code", name="uq_tariffs_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    duration_months: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("payload", name="uq_invoices_payload"),
        Index(
            "ix_invoices_provider_provider_invoice_id",
            "provider",
            "provider_invoice_id",
            unique=True,
            postgresql_where=text("provider_invoice_id IS NOT NULL"),
        ),
        Index(
            "ix_invoices_telegram_payment_charge_id",
            "telegram_payment_charge_id",
            unique=True,
            postgresql_where=text("telegram_payment_charge_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[InvoiceProvider] = mapped_column(
        enum_column(InvoiceProvider, "invoice_provider"),
        nullable=False,
    )
    provider_invoice_id: Mapped[str | None] = mapped_column(String(128))
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(128))
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id", ondelete="SET NULL"))
    promo_id: Mapped[int | None] = mapped_column(ForeignKey("promos.id", ondelete="SET NULL"))
    promo_code: Mapped[str | None] = mapped_column(String(64))
    promo_kind: Mapped[PromoKind | None] = mapped_column(enum_column(PromoKind, "promo_kind"))
    promo_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    promo_discount_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        server_default="0",
    )
    promo_extra_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    gross: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        enum_column(InvoiceStatus, "invoice_status"),
        nullable=False,
        server_default=InvoiceStatus.active.value,
        index=True,
    )
    payload: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    last_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"),
    )
    # Config plan code (not an FK — plans live in settings, like protected_chats).
    # Drives which chats the subscription unlocks; NULL falls back to all protected chats.
    plan_code: Mapped[str | None] = mapped_column(String(50))
    stars_subscription_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    source: Mapped[SubscriptionSource] = mapped_column(
        enum_column(SubscriptionSource, "subscription_source"),
        nullable=False,
        server_default=SubscriptionSource.manual.value,
    )
    notified_3d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_0d_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_post_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Promo(Base):
    __tablename__ = "promos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[PromoKind] = mapped_column(enum_column(PromoKind, "promo_kind"), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    allowed_tariffs: Mapped[list[str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("code", name="uq_promos_code"),
        Index("ix_promos_code_upper", func.upper(code), unique=True),
    )


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"
    __table_args__ = (
        UniqueConstraint("promo_id", "user_id", name="uq_promo_redemptions_promo_id_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promo_id: Mapped[int] = mapped_column(
        ForeignKey("promos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    referee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    custom_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))


class ReferralEarning(Base):
    __tablename__ = "referral_earnings"
    __table_args__ = (
        UniqueConstraint("invoice_id", name="uq_referral_earnings_invoice_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    referee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    paid_out: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    paid_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BroadcastLog(Base):
    __tablename__ = "broadcast_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    message_text: Mapped[str | None] = mapped_column(Text)
    target_filter: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderEvent(Base):
    __tablename__ = "provider_events"
    __table_args__ = (Index("ix_provider_events_provider_external_id", "provider", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[InvoiceProvider] = mapped_column(
        enum_column(InvoiceProvider, "provider_event_provider"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
