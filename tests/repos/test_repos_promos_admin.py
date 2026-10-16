from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from bot.models import PromoKind
from bot.repos.promos import (
    count_active_promos,
    create_promo,
    deactivate_promo,
    get_promo_by_code,
    list_active_promos,
)


async def test_create_promo_normalizes_code_and_is_fetchable(db_session) -> None:
    promo = await create_promo(
        db_session, code="save10", kind=PromoKind.percent, value=Decimal("10"), created_by=None
    )
    await db_session.commit()

    assert promo.id is not None
    assert promo.code == "SAVE10"
    assert promo.active is True
    fetched = await get_promo_by_code(db_session, "save10")
    assert fetched is not None and fetched.id == promo.id


async def test_create_promo_duplicate_code_raises(db_session) -> None:
    await create_promo(
        db_session, code="dup", kind=PromoKind.percent, value=Decimal("5"), created_by=None
    )
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await create_promo(
            db_session, code="DUP", kind=PromoKind.fixed_usd, value=Decimal("1"), created_by=None
        )


async def test_list_count_and_pagination(db_session) -> None:
    for i in range(7):
        await create_promo(
            db_session, code=f"c{i}", kind=PromoKind.percent, value=Decimal("5"), created_by=None
        )
    await db_session.commit()

    assert await count_active_promos(db_session) == 7
    page0 = await list_active_promos(db_session, limit=5, offset=0)
    page1 = await list_active_promos(db_session, limit=5, offset=5)
    assert len(page0) == 5
    assert len(page1) == 2
    assert page0[0].code == "C6"  # newest first (id desc)


async def test_deactivate_promo_idempotent_and_unknown(db_session) -> None:
    promo = await create_promo(
        db_session, code="x", kind=PromoKind.percent, value=Decimal("5"), created_by=None
    )
    await db_session.commit()

    assert await deactivate_promo(db_session, promo.id) is True
    assert await count_active_promos(db_session) == 0
    assert await deactivate_promo(db_session, promo.id) is False  # already inactive
    assert await deactivate_promo(db_session, 999_999) is False  # unknown id
