from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# One-time honest reset of loyalty status after switching the tier driver from purchased
# days to LIVED coverage (consumed = total_active_days - remaining days). Recomputes every
# user's status from what they have actually lived through, so statuses granted instantly by
# a bulk purchase under the old rule are lowered to the real tier. This is the only place a
# status is lowered; the runtime code ratchets (never downgrades) from here on.
revision = "20260701_0010"
down_revision = "20260701_0009"
branch_labels = None
depends_on = None

DAYS_PER_MONTH = 30
# Default loyalty thresholds (config.example: bronze month 2, silver 4, gold 6 -> 30/90/150
# lived-coverage days). If prod overrides these, runtime ratchet corrects upward on next
# activity and this baseline only ever errs on the safe (lower) side.
BRONZE_DAYS = (2 - 1) * DAYS_PER_MONTH
SILVER_DAYS = (4 - 1) * DAYS_PER_MONTH
GOLD_DAYS = (6 - 1) * DAYS_PER_MONTH

# Read side is typed so timestamptz round-trips as an aware datetime on both asyncpg (prod)
# and SQLite (tests). The write side does NOT type the status column as the ``user_status``
# enum: an enum-typed bind param makes asyncpg resolve the type at prepare time and fail with
# "type user_status does not exist", so status is written as a bare SQL string literal that
# Postgres coerces to the enum in assignment context (SQLite stores it as plain text).
users = sa.table(
    "users",
    sa.column("id", sa.Integer),
    sa.column("total_active_days", sa.Integer),
)
subscriptions = sa.table(
    "subscriptions",
    sa.column("user_id", sa.Integer),
    sa.column("expires_at", sa.DateTime(timezone=True)),
)

_TIERS = ("basic", "bronze", "silver", "gold")


def _tier_for_days(consumed: int) -> str:
    if consumed >= GOLD_DAYS:
        return "gold"
    if consumed >= SILVER_DAYS:
        return "silver"
    if consumed >= BRONZE_DAYS:
        return "bronze"
    return "basic"


def _remaining_days(expires_at: datetime | None, now: datetime) -> int:
    if expires_at is None:
        return 0
    aware = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    if aware <= now:
        return 0
    return max(0, (aware - now).days)


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    rows = bind.execute(
        sa.select(users.c.id, users.c.total_active_days, subscriptions.c.expires_at).select_from(
            users.outerjoin(subscriptions, subscriptions.c.user_id == users.c.id)
        )
    ).all()
    for user_id, total_active_days, expires_at in rows:
        consumed = max(0, (total_active_days or 0) - _remaining_days(expires_at, now))
        tier = _tier_for_days(consumed)
        assert tier in _TIERS  # noqa: S101 — guards the literal interpolated below
        bind.execute(
            sa.text(f"UPDATE users SET status = '{tier}' WHERE id = :id"),
            {"id": int(user_id)},
        )


def downgrade() -> None:
    # Historic per-user statuses are not recoverable (the old value was overwritten); leaving
    # the recomputed, honest statuses in place is the correct no-op on downgrade.
    pass
