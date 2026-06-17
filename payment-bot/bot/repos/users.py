from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.i18n import JsonI18n
from bot.models import User, UserStatus

SUPPORTED_LANGUAGES = frozenset({"ru", "ua", "en"})


def resolve_language(i18n: JsonI18n, language_code: str | None) -> str:
    return i18n.resolve_language(language_code)


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def upsert_user_from_telegram(
    session: AsyncSession,
    *,
    tg_id: int,
    username: str | None,
    first_name: str | None,
    language_code: str | None,
    i18n: JsonI18n,
) -> User:
    now = datetime.now(UTC)
    user = await get_user_by_tg_id(session, tg_id)

    if user is None:
        await _insert_user_if_missing(
            session,
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            lang_code=resolve_language(i18n, language_code),
            now=now,
        )
        user = await get_user_by_tg_id(session, tg_id)
        if user is None:
            msg = f"user with tg_id {tg_id} was not found after insert"
            raise LookupError(msg)

    user.username = username
    user.first_name = first_name
    if user.lang_code not in SUPPORTED_LANGUAGES:
        user.lang_code = resolve_language(i18n, language_code)
    user.status = user.status or UserStatus.basic
    user.total_active_days = user.total_active_days or 0
    user.created_at = user.created_at or now
    user.updated_at = now

    await session.flush()
    return user


async def update_user_language(session: AsyncSession, *, tg_id: int, lang_code: str) -> User:
    if lang_code not in SUPPORTED_LANGUAGES:
        msg = f"unsupported language: {lang_code}"
        raise ValueError(msg)

    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        msg = f"user with tg_id {tg_id} was not found"
        raise LookupError(msg)

    user.lang_code = lang_code
    user.updated_at = datetime.now(UTC)
    await session.flush()
    return user


async def ensure_minimal_user(
    session: AsyncSession,
    *,
    tg_id: int,
    i18n: JsonI18n,
    language_code: str | None = None,
) -> User:
    user = await get_user_by_tg_id(session, tg_id)
    if user is not None:
        return user

    now = datetime.now(UTC)
    await _insert_user_if_missing(
        session,
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code=resolve_language(i18n, language_code),
        now=now,
    )
    await session.flush()

    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        msg = f"user with tg_id {tg_id} was not found after insert"
        raise LookupError(msg)
    return user


async def _insert_user_if_missing(
    session: AsyncSession,
    *,
    tg_id: int,
    username: str | None,
    first_name: str | None,
    lang_code: str,
    now: datetime,
) -> None:
    statement = (
        insert(User)
        .values(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            lang_code=lang_code,
            status=UserStatus.basic,
            total_active_days=0,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[User.tg_id])
    )
    await session.execute(statement)
