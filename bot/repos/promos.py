from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.i18n import JsonI18n
from bot.models import Invoice, Promo, PromoKind, PromoRedemption, User
from bot.repos.users import ensure_minimal_user

if TYPE_CHECKING:
    from bot.services.pricing import PromoSnapshot


class PromoUnavailable(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_promo_code(code: str) -> str:
    return code.strip().upper()


def promo_snapshot_from_row(promo: Promo) -> PromoSnapshot:
    from bot.services.pricing import PromoSnapshot

    if promo.id is None:
        msg = "promo must have an id"
        raise ValueError(msg)
    return PromoSnapshot(
        promo_id=promo.id,
        code=promo.code,
        kind=promo.kind,
        value=promo.value,
    )


def validate_promo_available(
    promo: Promo,
    *,
    already_redeemed: bool,
    tariff_code: str | None,
    now: datetime,
) -> None:
    if not promo.active:
        raise PromoUnavailable("inactive")
    if promo.value <= 0:
        raise PromoUnavailable("invalid_value")
    if promo.kind is PromoKind.extra_days and promo.value != promo.value.to_integral_value():
        raise PromoUnavailable("invalid_value")
    if promo.expires_at is not None and promo.expires_at <= now:
        raise PromoUnavailable("expired")
    if promo.max_uses is not None and promo.uses >= promo.max_uses:
        raise PromoUnavailable("exhausted")
    if (
        tariff_code is not None
        and promo.allowed_tariffs
        and tariff_code not in promo.allowed_tariffs
    ):
        raise PromoUnavailable("tariff_not_allowed")
    if already_redeemed:
        raise PromoUnavailable("already_used")


async def apply_promo_code_for_user(
    session: AsyncSession,
    *,
    tg_id: int,
    code: str,
    language_code: str | None,
    i18n: JsonI18n,
) -> Promo:
    user = await ensure_minimal_user(
        session,
        tg_id=tg_id,
        i18n=i18n,
        language_code=language_code,
    )
    promo = await get_promo_by_code(session, code)
    if promo is None:
        raise PromoUnavailable("not_found")

    now = datetime.now(UTC)
    validate_promo_available(
        promo,
        already_redeemed=await user_has_redeemed_promo(session, user, promo),
        tariff_code=None,
        now=now,
    )
    user.pending_promo_id = promo.id
    await session.flush()
    return promo


async def get_promo_by_code(session: AsyncSession, code: str) -> Promo | None:
    normalized = normalize_promo_code(code)
    return await session.scalar(
        select(Promo).where(func.upper(Promo.code) == normalized),
    )


async def user_has_redeemed_promo(
    session: AsyncSession,
    user: User,
    promo: Promo,
) -> bool:
    if user.id is None or promo.id is None:
        return False
    redemption = await session.scalar(
        select(PromoRedemption).where(
            PromoRedemption.user_id == user.id,
            PromoRedemption.promo_id == promo.id,
        ),
    )
    return redemption is not None


async def get_pending_promo_for_user(
    session: AsyncSession,
    *,
    user: User,
    tariff_code: str,
    now: datetime,
    lock_user: bool,
) -> tuple[User, Promo | None]:
    if user.id is None:
        msg = "user must be persisted before resolving promo"
        raise ValueError(msg)

    statement = select(User).where(User.id == user.id)
    if lock_user:
        statement = statement.with_for_update()
    locked_user = await session.scalar(statement)
    if locked_user is None:
        msg = f"user with id {user.id} was not found"
        raise LookupError(msg)

    if locked_user.pending_promo_id is None:
        return locked_user, None

    promo_statement = select(Promo).where(Promo.id == locked_user.pending_promo_id)
    if lock_user:
        promo_statement = promo_statement.with_for_update()
    promo = await session.scalar(promo_statement)
    if promo is None:
        locked_user.pending_promo_id = None
        await session.flush()
        return locked_user, None

    try:
        validate_promo_available(
            promo,
            already_redeemed=await user_has_redeemed_promo(session, locked_user, promo),
            tariff_code=tariff_code,
            now=now,
        )
    except PromoUnavailable:
        locked_user.pending_promo_id = None
        await session.flush()
        return locked_user, None
    return locked_user, promo


async def get_pending_promo_snapshot_for_user(
    session: AsyncSession,
    *,
    user: User,
    tariff_code: str,
    now: datetime,
) -> PromoSnapshot | None:
    _, promo = await get_pending_promo_for_user(
        session,
        user=user,
        tariff_code=tariff_code,
        now=now,
        lock_user=False,
    )
    return promo_snapshot_from_row(promo) if promo is not None else None


async def reserve_promo_redemption(
    session: AsyncSession,
    *,
    promo: Promo,
    user: User,
    invoice: Invoice,
    now: datetime,
) -> PromoRedemption:
    if promo.id is None or user.id is None or invoice.id is None:
        msg = "promo, user, and invoice must be persisted before redemption"
        raise ValueError(msg)

    redemption = PromoRedemption(
        promo_id=promo.id,
        user_id=user.id,
        invoice_id=invoice.id,
        redeemed_at=now,
    )
    session.add(redemption)
    promo.uses = (promo.uses or 0) + 1
    user.pending_promo_id = None
    await session.flush()
    return redemption


async def release_promo_redemption_for_invoice(
    session: AsyncSession,
    invoice: Invoice,
) -> None:
    if invoice.id is None or invoice.promo_id is None:
        return

    redemption = await session.scalar(
        select(PromoRedemption).where(PromoRedemption.invoice_id == invoice.id),
    )
    if redemption is None:
        return

    promo = await session.scalar(select(Promo).where(Promo.id == redemption.promo_id))
    if promo is not None and promo.uses > 0:
        promo.uses -= 1

    user = await session.scalar(select(User).where(User.id == redemption.user_id).with_for_update())
    if user is not None and user.pending_promo_id is None:
        user.pending_promo_id = redemption.promo_id

    await session.delete(redemption)
    await session.flush()
