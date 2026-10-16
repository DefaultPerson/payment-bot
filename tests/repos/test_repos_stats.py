from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bot.models import (
    Invoice,
    InvoiceProvider,
    InvoiceStatus,
    ReferralEarning,
    Subscription,
    SubscriptionSource,
    User,
)
from bot.repos.stats import (
    count_new_users,
    get_active_user_counts,
    get_revenue_by_provider,
    get_revenue_totals,
    get_top_referrers,
)

NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)


def _invoice(
    uid: int, *, gross, net, fee, provider, paid_at, status=InvoiceStatus.paid, n
) -> Invoice:
    return Invoice(
        user_id=uid,
        provider=provider,
        amount_usd=Decimal(str(gross)),
        gross=Decimal(str(gross)),
        net=Decimal(str(net)),
        fee=Decimal(str(fee)),
        currency="USD",
        status=status,
        payload=f"pay-{n}",
        expires_at=NOW,
        paid_at=paid_at,
    )


async def _seed(session) -> dict[str, int]:
    u1 = User(
        tg_id=1001,
        username="alice",
        lang_code="en",
        created_at=NOW - timedelta(days=1),
        last_active_at=NOW - timedelta(hours=2),
    )
    u2 = User(
        tg_id=1002,
        username="bob",
        lang_code="en",
        created_at=NOW - timedelta(days=10),
        last_active_at=NOW - timedelta(days=10),
    )
    u3 = User(
        tg_id=1003,
        username=None,
        lang_code="en",
        created_at=NOW - timedelta(days=100),
        last_active_at=NOW - timedelta(days=100),
    )
    session.add_all([u1, u2, u3])
    await session.flush()

    session.add_all(
        [
            _invoice(
                u1.id,
                gross=100,
                net=90,
                fee=10,
                provider=InvoiceProvider.cryptobot,
                paid_at=NOW - timedelta(days=1),
                n=1,
            ),
            _invoice(
                u2.id,
                gross=50,
                net=48,
                fee=2,
                provider=InvoiceProvider.xrocket,
                paid_at=NOW - timedelta(days=2),
                n=2,
            ),
            _invoice(
                u3.id,
                gross=200,
                net=180,
                fee=20,
                provider=InvoiceProvider.cryptobot,
                paid_at=NOW - timedelta(days=100),
                n=3,
            ),
            _invoice(
                u1.id,
                gross=999,
                net=999,
                fee=0,
                provider=InvoiceProvider.cryptobot,
                paid_at=None,
                status=InvoiceStatus.active,
                n=4,
            ),
        ]
    )
    session.add(
        Subscription(
            user_id=u1.id,
            started_at=NOW - timedelta(days=5),
            expires_at=NOW + timedelta(days=5),
            source=SubscriptionSource.manual,
        )
    )
    session.add_all(
        [
            ReferralEarning(
                referrer_id=u1.id,
                referee_id=u2.id,
                invoice_id=1,
                amount_usd=Decimal("10"),
                created_at=NOW - timedelta(days=1),
            ),
            ReferralEarning(
                referrer_id=u1.id,
                referee_id=u3.id,
                invoice_id=2,
                amount_usd=Decimal("5"),
                created_at=NOW - timedelta(days=2),
            ),
            ReferralEarning(
                referrer_id=u2.id,
                referee_id=u3.id,
                invoice_id=3,
                amount_usd=Decimal("3"),
                created_at=NOW - timedelta(days=1),
            ),
        ]
    )
    await session.commit()
    return {"u1": u1.id, "u2": u2.id, "u3": u3.id}


async def test_revenue_totals_window_and_all(db_session) -> None:
    await _seed(db_session)

    week = await get_revenue_totals(db_session, start=NOW - timedelta(days=7), end=NOW)
    assert (week.gross, week.net, week.fee, week.paid_count) == (
        Decimal("150.00"),
        Decimal("138.00"),
        Decimal("12.00"),
        2,
    )

    everything = await get_revenue_totals(db_session, start=None, end=NOW)
    assert (everything.gross, everything.paid_count) == (Decimal("350.00"), 3)


async def test_revenue_totals_empty_is_zero(db_session) -> None:
    totals = await get_revenue_totals(db_session, start=None, end=NOW)
    assert (totals.gross, totals.net, totals.fee, totals.paid_count) == (
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        0,
    )


async def test_revenue_by_provider_grouped_desc(db_session) -> None:
    await _seed(db_session)
    rows = await get_revenue_by_provider(db_session, start=None, end=NOW)
    by = {row.provider: row for row in rows}
    assert by[InvoiceProvider.cryptobot].gross == Decimal("300.00")
    assert by[InvoiceProvider.cryptobot].paid_count == 2
    assert by[InvoiceProvider.xrocket].gross == Decimal("50.00")
    # ordered by gross desc
    assert rows[0].provider is InvoiceProvider.cryptobot


async def test_count_new_users(db_session) -> None:
    await _seed(db_session)
    assert await count_new_users(db_session, start=NOW - timedelta(days=7), end=NOW) == 1
    assert await count_new_users(db_session, start=None, end=NOW) == 3


async def test_top_referrers_ranked(db_session) -> None:
    ids = await _seed(db_session)
    top = await get_top_referrers(db_session, start=None, end=NOW, limit=5)
    assert top[0].referrer_id == ids["u1"]
    assert top[0].earned_usd == Decimal("15.00")
    assert top[0].referral_count == 2  # distinct referees
    assert top[0].username == "alice"
    assert top[1].referrer_id == ids["u2"]


async def test_active_user_counts_dau_mau(db_session) -> None:
    await _seed(db_session)
    dau, mau = await get_active_user_counts(db_session, now=NOW)
    assert dau == 1
    assert mau == 2
