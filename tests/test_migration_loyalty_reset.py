"""Reset migration (0010): recompute loyalty status from lived coverage, honestly.

Verifies the one-time backfill lowers statuses that were flash-granted by a bulk purchase
while preserving statuses genuinely earned by lived coverage.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bot.models import Base, Subscription, SubscriptionSource, User, UserStatus

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "20260701_0010_loyalty_status_reset.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("loyalty_reset_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reset_migration_recomputes_status_from_lived_coverage(monkeypatch) -> None:
    migration = _load_migration()
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)

    with Session(engine) as session:
        # (1) Bulk buyer flashed to Gold but has lived almost none of the 6m -> honest basic.
        bulk = User(tg_id=1, status=UserStatus.gold, total_active_days=180)
        # (2) Lived ~5 months of a 6m plan (5 days remaining) -> genuinely Gold, kept.
        earned = User(tg_id=2, status=UserStatus.gold, total_active_days=180)
        # (3) Stale Silver, no active subscription -> all 100 days lived, keeps Silver.
        lapsed = User(tg_id=3, status=UserStatus.silver, total_active_days=100)
        session.add_all([bulk, earned, lapsed])
        session.flush()
        ids = (bulk.id, earned.id, lapsed.id)
        session.add_all(
            [
                Subscription(
                    user_id=bulk.id,
                    started_at=now,
                    expires_at=now + timedelta(days=175),
                    source=SubscriptionSource.cryptobot,
                ),
                Subscription(
                    user_id=earned.id,
                    started_at=now,
                    expires_at=now + timedelta(days=5),
                    source=SubscriptionSource.cryptobot,
                ),
            ]
        )
        session.commit()

    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: conn))
        migration.upgrade()

    bulk_id, earned_id, lapsed_id = ids
    with Session(engine) as session:
        assert session.get(User, bulk_id).status is UserStatus.basic
        assert session.get(User, earned_id).status is UserStatus.gold
        assert session.get(User, lapsed_id).status is UserStatus.silver
    engine.dispose()
