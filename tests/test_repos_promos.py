from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from bot.models import (
    Invoice,
    InvoiceProvider,
    InvoiceStatus,
    Promo,
    PromoKind,
    PromoRedemption,
    User,
    UserStatus,
)
from bot.repos.promos import (
    PromoUnavailable,
    apply_promo_code_for_user,
    get_pending_promo_for_menu,
    get_pending_promo_for_user,
    get_promo_by_code,
    promo_snapshot_from_row,
    release_promo_redemption_for_invoice,
    reserve_promo_redemption,
    validate_promo_available,
)


def make_promo(
    *,
    kind: PromoKind = PromoKind.percent,
    value: Decimal = Decimal("20"),
    active: bool = True,
    uses: int = 0,
    max_uses: int | None = None,
    expires_at: datetime | None = None,
    allowed_tariffs: list[str] | None = None,
) -> Promo:
    timestamp = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    return Promo(
        id=5,
        code="SAVE20",
        kind=kind,
        value=value,
        max_uses=max_uses,
        uses=uses,
        expires_at=expires_at,
        created_by=None,
        active=active,
        allowed_tariffs=allowed_tariffs,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_user(*, pending_promo_id: int | None = None) -> User:
    timestamp = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    return User(
        id=10,
        tg_id=700,
        username=None,
        first_name=None,
        lang_code="en",
        status=UserStatus.basic,
        total_active_days=0,
        referrer_id=None,
        referral_percent=None,
        pending_promo_id=pending_promo_id,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_invoice() -> Invoice:
    timestamp = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    return Invoice(
        id=30,
        user_id=10,
        provider=InvoiceProvider.cryptobot,
        provider_invoice_id=None,
        telegram_payment_charge_id=None,
        tariff_id=20,
        promo_id=5,
        promo_code="SAVE20",
        promo_kind=PromoKind.percent,
        promo_value=Decimal("20"),
        promo_discount_usd=Decimal("10.00"),
        promo_extra_days=0,
        amount_usd=Decimal("40.00"),
        gross=Decimal("40.00"),
        fee=Decimal("1.20"),
        net=Decimal("38.80"),
        currency="USD",
        status=InvoiceStatus.active,
        payload="inv:test",
        provider_payload=None,
        expires_at=timestamp,
        created_at=timestamp,
        paid_at=None,
    )


class FakeScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def all(self) -> list[Any]:
        return self.items


class FakePromoSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.promos: list[Promo] = []
        self.redemptions: list[PromoRedemption] = []
        self.flushed = 0
        self.added: list[Any] = []

    def add(self, instance: Any) -> None:
        self.added.append(instance)
        if isinstance(instance, PromoRedemption):
            self.redemptions.append(instance)

    async def delete(self, instance: Any) -> None:
        if isinstance(instance, PromoRedemption):
            self.redemptions.remove(instance)

    async def flush(self) -> None:
        self.flushed += 1

    async def scalar(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params

        if entity is User:
            user_id = _first_param(params, "id")
            tg_id = _first_param(params, "tg_id")
            if user_id is not None:
                return next((user for user in self.users if user.id == user_id), None)
            if tg_id is not None:
                return next((user for user in self.users if user.tg_id == tg_id), None)

        if entity is Promo:
            promo_id = _first_param(params, "id")
            code = _first_string_param(params)
            if promo_id is not None:
                return next((promo for promo in self.promos if promo.id == promo_id), None)
            if code is not None:
                return next((promo for promo in self.promos if promo.code.upper() == code), None)

        if entity is PromoRedemption:
            user_id = _first_param(params, "user_id")
            promo_id = _first_param(params, "promo_id")
            invoice_id = _first_param(params, "invoice_id")
            return next(
                (
                    redemption
                    for redemption in self.redemptions
                    if (user_id is None or redemption.user_id == user_id)
                    and (promo_id is None or redemption.promo_id == promo_id)
                    and (invoice_id is None or redemption.invoice_id == invoice_id)
                ),
                None,
            )

        msg = f"unsupported fake scalar query for {entity}"
        raise AssertionError(msg)


def _first_param(params: dict[str, Any], prefix: str) -> Any:
    return next((value for key, value in params.items() if key.startswith(prefix)), None)


def _first_string_param(params: dict[str, Any]) -> str | None:
    return next((value for value in params.values() if isinstance(value, str)), None)


def test_validate_promo_available_accepts_active_unredeemed_tariff_match() -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    promo = make_promo(max_uses=10, allowed_tariffs=["1m", "3m"])

    validate_promo_available(promo, already_redeemed=False, tariff_code="3m", now=now)


@pytest.mark.parametrize(
    ("promo", "already_redeemed", "expected_reason"),
    [
        (make_promo(active=False), False, "inactive"),
        (make_promo(expires_at=datetime(2026, 6, 18, 11, 59, tzinfo=UTC)), False, "expired"),
        (make_promo(uses=2, max_uses=2), False, "exhausted"),
        (make_promo(value=Decimal("-1")), False, "invalid_value"),
        (make_promo(kind=PromoKind.extra_days, value=Decimal("0.5")), False, "invalid_value"),
        (make_promo(allowed_tariffs=["12m"]), False, "tariff_not_allowed"),
        (make_promo(), True, "already_used"),
    ],
)
def test_validate_promo_available_rejects_unusable_promos(
    promo: Promo,
    already_redeemed: bool,
    expected_reason: str,
) -> None:
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)

    with pytest.raises(PromoUnavailable) as exc_info:
        validate_promo_available(
            promo,
            already_redeemed=already_redeemed,
            tariff_code="3m",
            now=now,
        )

    assert exc_info.value.reason == expected_reason


def test_promo_snapshot_from_row_preserves_payment_effect_metadata() -> None:
    promo = make_promo(
        kind=PromoKind.extra_days,
        value=Decimal("7"),
        expires_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
    )

    snapshot = promo_snapshot_from_row(promo)

    assert snapshot.promo_id == 5
    assert snapshot.code == "SAVE20"
    assert snapshot.kind is PromoKind.extra_days
    assert snapshot.value == Decimal("7")


async def test_get_promo_by_code_is_case_insensitive() -> None:
    session = FakePromoSession()
    promo = make_promo()
    session.promos.append(promo)

    assert await get_promo_by_code(session, "save20") is promo


async def test_apply_promo_code_for_user_sets_pending_promo() -> None:
    session = FakePromoSession()
    user = make_user()
    promo = make_promo()
    session.users.append(user)
    session.promos.append(promo)

    applied = await apply_promo_code_for_user(
        session,
        tg_id=700,
        code="save20",
        language_code="en",
        i18n=object(),
    )

    assert applied is promo
    assert user.pending_promo_id == promo.id
    assert session.flushed == 1


async def test_get_pending_promo_for_user_clears_unusable_pending_promo() -> None:
    session = FakePromoSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    user = make_user(pending_promo_id=5)
    promo = make_promo(expires_at=now)
    session.users.append(user)
    session.promos.append(promo)

    locked_user, pending = await get_pending_promo_for_user(
        session,
        user=user,
        tariff_code="1m",
        now=now,
        lock_user=True,
    )

    assert locked_user is user
    assert pending is None
    assert user.pending_promo_id is None
    assert session.flushed == 1


async def test_get_pending_promo_for_menu_keeps_tariff_restricted_promo() -> None:
    # Regression: browsing the duration menu must NOT drop a valid promo just because it is
    # restricted to tariffs other than the ones being priced. get_pending_promo_for_menu resolves
    # with tariff_code=None, so the tariff_not_allowed clear never fires here.
    session = FakePromoSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    user = make_user(pending_promo_id=5)
    promo = make_promo(allowed_tariffs=["6m"])
    session.users.append(user)
    session.promos.append(promo)

    snapshot, allowed = await get_pending_promo_for_menu(session, user=user, now=now)

    assert snapshot is not None
    assert snapshot.code == "SAVE20"
    assert allowed == frozenset({"6m"})
    assert user.pending_promo_id == 5  # preserved — not wiped by viewing the menu
    assert session.flushed == 0


async def test_get_pending_promo_for_user_clears_missing_pending_promo() -> None:
    session = FakePromoSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    user = make_user(pending_promo_id=404)
    session.users.append(user)

    locked_user, pending = await get_pending_promo_for_user(
        session,
        user=user,
        tariff_code="1m",
        now=now,
        lock_user=True,
    )

    assert locked_user is user
    assert pending is None
    assert user.pending_promo_id is None
    assert session.flushed == 1


async def test_reserve_promo_redemption_increments_uses_and_clears_pending() -> None:
    session = FakePromoSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    user = make_user(pending_promo_id=5)
    promo = make_promo(uses=1)
    invoice = make_invoice()

    redemption = await reserve_promo_redemption(
        session,
        promo=promo,
        user=user,
        invoice=invoice,
        now=now,
    )

    assert redemption in session.redemptions
    assert redemption.promo_id == promo.id
    assert redemption.user_id == user.id
    assert redemption.invoice_id == invoice.id
    assert promo.uses == 2
    assert user.pending_promo_id is None
    assert session.flushed == 1


async def test_release_promo_redemption_for_invoice_restores_pending_and_uses() -> None:
    session = FakePromoSession()
    user = make_user(pending_promo_id=None)
    promo = make_promo(uses=2)
    invoice = make_invoice()
    redemption = PromoRedemption(
        id=50,
        promo_id=promo.id,
        user_id=user.id,
        invoice_id=invoice.id,
        redeemed_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
    )
    session.users.append(user)
    session.promos.append(promo)
    session.redemptions.append(redemption)

    await release_promo_redemption_for_invoice(session, invoice)

    assert redemption not in session.redemptions
    assert promo.uses == 1
    assert user.pending_promo_id == promo.id
    assert session.flushed == 1
