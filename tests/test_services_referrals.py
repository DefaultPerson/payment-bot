from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bot.models import Referral, User, UserStatus
from bot.services.referrals import (
    ReferralBindStatus,
    bind_referral_from_start,
    parse_referral_payload,
)

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


class FakeReferralSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.referrals: list[Referral] = []
        self.flushed = 0
        self._next_referral_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, Referral) and instance not in self.referrals:
            self.referrals.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for referral in self.referrals:
            if referral.id is None:
                referral.id = self._next_referral_id
                self._next_referral_id += 1

    async def scalar(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params

        if entity is User:
            tg_id = _param(params, "tg_id")
            user_id = _param(params, "id")
            if tg_id is not None:
                return next((user for user in self.users if user.tg_id == tg_id), None)
            if user_id is not None:
                return next((user for user in self.users if user.id == user_id), None)

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

        msg = f"unsupported scalar query for {entity}"
        raise AssertionError(msg)


def make_user(*, user_id: int, tg_id: int, referrer_id: int | None = None) -> User:
    return User(
        id=user_id,
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code="en",
        status=UserStatus.basic,
        total_active_days=0,
        referrer_id=referrer_id,
        referral_percent=None,
        pending_promo_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_parse_referral_payload_accepts_start_deep_links() -> None:
    assert parse_referral_payload("/start ref_1001") == 1001
    assert parse_referral_payload("/start@HydraBot ref_1001") == 1001


def test_parse_referral_payload_rejects_invalid_or_missing_links() -> None:
    assert parse_referral_payload(None) is None
    assert parse_referral_payload("/start") is None
    assert parse_referral_payload("/start ref_self") is None
    assert parse_referral_payload("/promo ref_1001") is None


async def test_bind_referral_from_start_creates_valid_relation() -> None:
    session = FakeReferralSession()
    referrer = make_user(user_id=1, tg_id=1001)
    referee = make_user(user_id=2, tg_id=2002)
    session.users.extend([referrer, referee])

    result = await bind_referral_from_start(
        session,
        referee=referee,
        referrer_tg_id=1001,
        now=NOW,
        allow_referrer_change=False,
    )

    assert result.status is ReferralBindStatus.applied
    assert result.referrer_tg_id == 1001
    assert referee.referrer_id == 1
    assert session.referrals[0].referrer_id == 1
    assert session.referrals[0].referee_id == 2


async def test_bind_referral_from_start_ignores_self_referral() -> None:
    session = FakeReferralSession()
    referee = make_user(user_id=2, tg_id=2002)
    session.users.append(referee)

    result = await bind_referral_from_start(
        session,
        referee=referee,
        referrer_tg_id=2002,
        now=NOW,
        allow_referrer_change=False,
    )

    assert result.status is ReferralBindStatus.ignored
    assert referee.referrer_id is None
    assert session.referrals == []


async def test_bind_referral_from_start_ignores_direct_cycles() -> None:
    session = FakeReferralSession()
    referee = make_user(user_id=2, tg_id=2002)
    referrer = make_user(user_id=1, tg_id=1001, referrer_id=2)
    session.users.extend([referrer, referee])

    result = await bind_referral_from_start(
        session,
        referee=referee,
        referrer_tg_id=1001,
        now=NOW,
        allow_referrer_change=False,
    )

    assert result.status is ReferralBindStatus.ignored
    assert referee.referrer_id is None
    assert session.referrals == []


async def test_bind_referral_from_start_rejects_rebind_when_disabled() -> None:
    session = FakeReferralSession()
    referee = make_user(user_id=2, tg_id=2002, referrer_id=9)
    new_referrer = make_user(user_id=1, tg_id=1001)
    session.users.extend([new_referrer, referee])

    result = await bind_referral_from_start(
        session,
        referee=referee,
        referrer_tg_id=1001,
        now=NOW,
        allow_referrer_change=False,
    )

    assert result.status is ReferralBindStatus.rebind_rejected
    assert referee.referrer_id == 9
    assert session.referrals == []


def _param(params: dict[str, Any], prefix: str) -> Any:
    return next((value for key, value in params.items() if key.startswith(prefix)), None)
