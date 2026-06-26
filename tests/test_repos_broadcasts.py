from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bot.models import Subscription, SubscriptionSource, User
from bot.repos.broadcasts import (
    create_broadcast_log,
    finish_broadcast_log,
    get_broadcast_targets,
)

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


async def _seed_users_and_subs(session) -> None:
    u1 = User(tg_id=1001, lang_code="en")
    u2 = User(tg_id=1002, lang_code="ru")
    u3 = User(tg_id=1003, lang_code="en")
    session.add_all([u1, u2, u3])
    await session.flush()
    session.add(
        Subscription(
            user_id=u1.id,
            started_at=NOW - timedelta(days=30),
            expires_at=NOW + timedelta(days=1),
            source=SubscriptionSource.manual,
        )
    )
    session.add(
        Subscription(
            user_id=u2.id,
            started_at=NOW - timedelta(days=30),
            expires_at=NOW - timedelta(seconds=1),
            source=SubscriptionSource.manual,
        )
    )
    await session.commit()


async def test_get_broadcast_targets_segments_users_by_subscription_state(db_session) -> None:
    await _seed_users_and_subs(db_session)

    assert await get_broadcast_targets(db_session, segment="all", now=NOW) == [1001, 1002, 1003]
    assert await get_broadcast_targets(db_session, segment="active", now=NOW) == [1001]
    assert await get_broadcast_targets(db_session, segment="inactive", now=NOW) == [1002, 1003]


async def test_get_broadcast_targets_rejects_unknown_segment(db_session) -> None:
    with pytest.raises(ValueError, match="unknown broadcast segment"):
        await get_broadcast_targets(db_session, segment="vip", now=NOW)


async def test_broadcast_log_start_and_finish_accounting(db_session) -> None:
    log = await create_broadcast_log(
        db_session,
        admin_user_id=None,
        message_text="hello",
        target_filter={"segment": "all"},
        started_at=NOW,
    )
    await finish_broadcast_log(
        db_session,
        log,
        total_count=3,
        success_count=2,
        failed_count=1,
        finished_at=NOW + timedelta(seconds=1),
    )

    assert log.id is not None
    assert log.message_text == "hello"
    assert log.target_filter == {"segment": "all"}
    assert (log.total_count, log.success_count, log.failed_count) == (3, 2, 1)
