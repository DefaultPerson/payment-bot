from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from bot.models import Referral, ReferralEarning, User, UserStatus
from bot.repos.referrals import (
    InsufficientReferralBalance,
    create_referral,
    create_referral_earning_once,
    get_referral_by_referee_id,
    get_referral_stats,
    reset_referral_balance,
    set_custom_percent_for_referrer,
    spend_referral_balance,
)

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


class FakeScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def all(self) -> list[Any]:
        return self.items


class FakeReferralSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.referrals: list[Referral] = []
        self.earnings: list[ReferralEarning] = []
        self.flushed = 0
        self.executed_statements: list[Any] = []
        self._next_referral_id = 1
        self._next_earning_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, Referral) and instance not in self.referrals:
            self.referrals.append(instance)
        if isinstance(instance, ReferralEarning) and instance not in self.earnings:
            self.earnings.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for referral in self.referrals:
            if referral.id is None:
                referral.id = self._next_referral_id
                self._next_referral_id += 1
        for earning in self.earnings:
            if earning.id is None:
                earning.id = self._next_earning_id
                self._next_earning_id += 1

    async def execute(self, statement: Any) -> None:
        self.executed_statements.append(statement)
        if getattr(getattr(statement, "table", None), "name", None) != "referral_earnings":
            msg = f"unsupported fake execute for {statement}"
            raise AssertionError(msg)

        params = statement.compile(dialect=postgresql.dialect()).params
        if any(earning.invoice_id == params["invoice_id"] for earning in self.earnings):
            return
        earning = ReferralEarning(
            id=self._next_earning_id,
            referrer_id=params["referrer_id"],
            referee_id=params["referee_id"],
            invoice_id=params["invoice_id"],
            amount_usd=params["amount_usd"],
            created_at=params["created_at"],
            paid_out=params["paid_out"],
            paid_out_at=params["paid_out_at"],
        )
        self._next_earning_id += 1
        self.earnings.append(earning)

    async def scalar(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params

        if entity is Referral:
            referee_id = _param(params, "referee_id")
            return next(
                (
                    referral
                    for referral in self.referrals
                    if referee_id is None or referral.referee_id == referee_id
                ),
                None,
            )

        if entity is ReferralEarning:
            invoice_id = _param(params, "invoice_id")
            return next(
                (
                    earning
                    for earning in self.earnings
                    if invoice_id is None or earning.invoice_id == invoice_id
                ),
                None,
            )

        if entity is User:
            user_id = _param(params, "id")
            user = next((candidate for candidate in self.users if candidate.id == user_id), None)
            return user.referral_spent_usd if user is not None else None

        msg = f"unsupported scalar query for {entity}"
        raise AssertionError(msg)

    async def scalars(self, statement: Any) -> FakeScalarResult:
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params

        if entity is Referral:
            referrer_id = _param(params, "referrer_id")
            return FakeScalarResult(
                [
                    referral
                    for referral in self.referrals
                    if referrer_id is None or referral.referrer_id == referrer_id
                ]
            )

        if entity is ReferralEarning:
            referrer_id = _param(params, "referrer_id")
            return FakeScalarResult(
                [
                    earning
                    for earning in self.earnings
                    if referrer_id is None or earning.referrer_id == referrer_id
                ]
            )

        msg = f"unsupported scalars query for {entity}"
        raise AssertionError(msg)


def make_user(
    *,
    user_id: int,
    tg_id: int,
    referrer_id: int | None = None,
    referral_percent: Decimal | None = None,
) -> User:
    return User(
        id=user_id,
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code="en",
        status=UserStatus.basic,
        total_active_days=0,
        referrer_id=referrer_id,
        referral_percent=referral_percent,
        pending_promo_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def make_referral(
    *,
    referrer_id: int = 1,
    referee_id: int = 2,
    custom_percent: Decimal | None = None,
) -> Referral:
    return Referral(
        id=None,
        referrer_id=referrer_id,
        referee_id=referee_id,
        joined_at=NOW,
        custom_percent=custom_percent,
    )


def make_earning(
    *,
    referrer_id: int = 1,
    referee_id: int = 2,
    invoice_id: int = 30,
    amount_usd: Decimal = Decimal("7.11"),
    paid_out: bool = False,
) -> ReferralEarning:
    return ReferralEarning(
        id=None,
        referrer_id=referrer_id,
        referee_id=referee_id,
        invoice_id=invoice_id,
        amount_usd=amount_usd,
        created_at=NOW,
        paid_out=paid_out,
        paid_out_at=None,
    )


async def test_create_referral_sets_user_referrer_and_relation() -> None:
    session = FakeReferralSession()
    referrer = make_user(user_id=1, tg_id=1001, referral_percent=Decimal("15"))
    referee = make_user(user_id=2, tg_id=2002)

    referral = await create_referral(
        session,
        referrer=referrer,
        referee=referee,
        joined_at=NOW,
    )

    assert referral.id == 1
    assert referral.referrer_id == 1
    assert referral.referee_id == 2
    assert referral.joined_at == NOW
    assert referral.custom_percent == Decimal("15")
    assert referee.referrer_id == 1
    assert session.referrals == [referral]
    assert session.flushed == 1


async def test_get_referral_by_referee_id_returns_existing_relation() -> None:
    session = FakeReferralSession()
    referral = make_referral(referrer_id=1, referee_id=2)
    session.referrals.append(referral)

    assert await get_referral_by_referee_id(session, 2) is referral


async def test_get_referral_stats_counts_referrals_and_sums_earnings() -> None:
    session = FakeReferralSession()
    session.referrals.extend(
        [
            make_referral(referrer_id=1, referee_id=2),
            make_referral(referrer_id=1, referee_id=3),
            make_referral(referrer_id=9, referee_id=4),
        ]
    )
    session.earnings.extend(
        [
            make_earning(referrer_id=1, referee_id=2, invoice_id=30, amount_usd=Decimal("7.11")),
            make_earning(referrer_id=1, referee_id=3, invoice_id=31, amount_usd=Decimal("3.89")),
            make_earning(
                referrer_id=1,
                referee_id=3,
                invoice_id=33,
                amount_usd=Decimal("2.00"),
                paid_out=True,
            ),
            make_earning(referrer_id=9, referee_id=4, invoice_id=32, amount_usd=Decimal("5.00")),
        ]
    )

    stats = await get_referral_stats(session, referrer_id=1)

    assert stats.referral_count == 2
    assert stats.earned_usd == Decimal("13.00")
    assert stats.unpaid_earned_usd == Decimal("11.00")


async def test_create_referral_earning_once_is_idempotent_by_invoice_id() -> None:
    session = FakeReferralSession()

    first = await create_referral_earning_once(
        session,
        referrer_id=1,
        referee_id=2,
        invoice_id=30,
        amount_usd=Decimal("7.11"),
        created_at=NOW,
    )
    second = await create_referral_earning_once(
        session,
        referrer_id=1,
        referee_id=2,
        invoice_id=30,
        amount_usd=Decimal("9.99"),
        created_at=NOW,
    )

    assert first is second
    assert first.amount_usd == Decimal("7.11")
    assert session.earnings == [first]
    assert len(session.executed_statements) == 1


async def test_set_custom_percent_for_referrer_updates_existing_referrals() -> None:
    session = FakeReferralSession()
    referrer = make_user(user_id=1, tg_id=1001)
    first = make_referral(referrer_id=1, referee_id=2)
    second = make_referral(referrer_id=1, referee_id=3)
    other = make_referral(referrer_id=9, referee_id=4)
    session.users.append(referrer)
    session.referrals.extend([first, second, other])

    updated = await set_custom_percent_for_referrer(
        session,
        referrer=referrer,
        percent=Decimal("15"),
    )

    assert updated == 2
    assert referrer.referral_percent == Decimal("15")
    assert first.custom_percent == Decimal("15")
    assert second.custom_percent == Decimal("15")
    assert other.custom_percent is None
    assert session.flushed == 1


def _param(params: dict[str, Any], prefix: str) -> Any:
    return next((value for key, value in params.items() if key.startswith(prefix)), None)


async def test_spend_referral_balance_debits_and_reduces_available(session_factory: Any) -> None:
    async with session_factory() as session:
        referrer = User(tg_id=1001, lang_code="en")
        referee = User(tg_id=1002, lang_code="en")
        session.add_all([referrer, referee])
        await session.flush()
        session.add_all(
            [
                ReferralEarning(
                    referrer_id=referrer.id,
                    referee_id=referee.id,
                    invoice_id=1,
                    amount_usd=Decimal("20.00"),
                    created_at=NOW,
                ),
                ReferralEarning(
                    referrer_id=referrer.id,
                    referee_id=referee.id,
                    invoice_id=2,
                    amount_usd=Decimal("5.00"),
                    created_at=NOW,
                ),
            ]
        )
        await session.flush()

        remaining = await spend_referral_balance(session, user=referrer, amount=Decimal("15.00"))

        assert remaining == Decimal("10.00")
        assert referrer.referral_spent_usd == Decimal("15.00")
        stats = await get_referral_stats(session, referrer_id=referrer.id)
        assert stats.spent_usd == Decimal("15.00")
        assert stats.available_usd == Decimal("10.00")


async def test_spend_referral_balance_rejects_when_short(session_factory: Any) -> None:
    async with session_factory() as session:
        referrer = User(tg_id=2001, lang_code="en")
        referee = User(tg_id=2002, lang_code="en")
        session.add_all([referrer, referee])
        await session.flush()
        session.add(
            ReferralEarning(
                referrer_id=referrer.id,
                referee_id=referee.id,
                invoice_id=3,
                amount_usd=Decimal("5.00"),
                created_at=NOW,
            )
        )
        await session.flush()

        with pytest.raises(InsufficientReferralBalance) as excinfo:
            await spend_referral_balance(session, user=referrer, amount=Decimal("10.00"))

        assert excinfo.value.available == Decimal("5.00")
        assert (referrer.referral_spent_usd or Decimal("0.00")) == Decimal("0.00")


async def test_reset_referral_balance_zeros_available(session_factory: Any) -> None:
    async with session_factory() as session:
        referrer = User(tg_id=3001, lang_code="en")
        referee = User(tg_id=3002, lang_code="en")
        session.add_all([referrer, referee])
        await session.flush()
        session.add_all(
            [
                ReferralEarning(
                    referrer_id=referrer.id,
                    referee_id=referee.id,
                    invoice_id=10,
                    amount_usd=Decimal("20.00"),
                    created_at=NOW,
                ),
                ReferralEarning(
                    referrer_id=referrer.id,
                    referee_id=referee.id,
                    invoice_id=11,
                    amount_usd=Decimal("5.00"),
                    created_at=NOW,
                ),
            ]
        )
        await session.flush()
        await spend_referral_balance(session, user=referrer, amount=Decimal("4.00"))

        cleared = await reset_referral_balance(session, referrer=referrer, now=NOW)

        assert cleared == 2  # both outstanding earnings settled
        assert referrer.referral_spent_usd == Decimal("0.00")
        stats = await get_referral_stats(session, referrer_id=referrer.id)
        assert stats.earned_usd == Decimal("25.00")  # lifetime history preserved
        assert stats.unpaid_earned_usd == Decimal("0.00")
        assert stats.spent_usd == Decimal("0.00")
        assert stats.available_usd == Decimal("0.00")
