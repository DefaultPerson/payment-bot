from sqlalchemy import UniqueConstraint

from bot.models import (
    Base,
    BroadcastLog,
    Invoice,
    InvoiceProvider,
    InvoiceStatus,
    Promo,
    PromoKind,
    PromoRedemption,
    ProviderEvent,
    Referral,
    ReferralEarning,
    SchemaVersionMarker,
    Subscription,
    SubscriptionSource,
    Tariff,
    User,
    UserStatus,
)


def unique_constraint_columns(table_name: str, constraint_name: str) -> tuple[str, ...]:
    table = Base.metadata.tables[table_name]
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.name == constraint_name:
            return tuple(column.name for column in constraint.columns)
    msg = f"{constraint_name} unique constraint not found on {table_name}"
    raise AssertionError(msg)


def index_names(table_name: str) -> set[str]:
    table = Base.metadata.tables[table_name]
    return {index.name for index in table.indexes}


def test_phase1_models_are_registered_in_metadata() -> None:
    assert Base is not None
    assert SchemaVersionMarker is not None
    assert User is not None
    assert Subscription is not None
    assert Tariff is not None
    assert Invoice is not None
    assert Promo is not None
    assert PromoRedemption is not None
    assert Referral is not None
    assert ReferralEarning is not None
    assert BroadcastLog is not None
    assert ProviderEvent is not None

    assert {
        "schema_version_marker",
        "users",
        "subscriptions",
        "tariffs",
        "invoices",
        "promos",
        "promo_redemptions",
        "referrals",
        "referral_earnings",
        "broadcast_logs",
        "provider_events",
    }.issubset(Base.metadata.tables)


def test_phase1_enum_values_are_stable() -> None:
    assert UserStatus.basic.value == "basic"
    assert UserStatus.bronze.value == "bronze"
    assert SubscriptionSource.manual.value == "manual"
    assert InvoiceProvider.cryptobot.value == "cryptobot"
    assert InvoiceStatus.active.value == "active"
    assert PromoKind.extra_days.value == "extra_days"


def test_named_unique_constraints_match_phase1_migration() -> None:
    assert unique_constraint_columns("tariffs", "uq_tariffs_code") == ("code",)
    assert unique_constraint_columns("invoices", "uq_invoices_payload") == ("payload",)
    assert unique_constraint_columns("promos", "uq_promos_code") == ("code",)


def test_phase5_referral_earnings_are_unique_per_invoice() -> None:
    assert "referral_percent" in Base.metadata.tables["users"].c
    assert unique_constraint_columns("referral_earnings", "uq_referral_earnings_invoice_id") == (
        "invoice_id",
    )


def test_phase3_models_include_pending_promo_and_invoice_snapshot_columns() -> None:
    users = Base.metadata.tables["users"]
    invoices = Base.metadata.tables["invoices"]

    assert "pending_promo_id" in users.c
    assert "promo_id" in invoices.c
    assert "promo_code" in invoices.c
    assert "promo_kind" in invoices.c
    assert "promo_value" in invoices.c
    assert "promo_discount_usd" in invoices.c
    assert "promo_extra_days" in invoices.c


def test_phase3_promos_have_case_insensitive_code_index() -> None:
    assert "ix_promos_code_upper" in index_names("promos")
