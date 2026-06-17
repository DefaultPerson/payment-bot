from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.i18n import JsonI18n
from bot.models import User, UserStatus

SUPPORTED_LANGUAGES = frozenset({"ru", "ua", "en"})


def resolve_language(i18n: JsonI18n, language_code: str | None) -> str:
    return i18n.resolve_language(language_code)


def _resolve_supplied_supported_language(i18n: JsonI18n, language_code: str | None) -> str | None:
    normalized = i18n.normalize_language(language_code)
    if normalized in i18n.languages:
        return normalized
    return None


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
        user = User(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            lang_code=resolve_language(i18n, language_code),
            status=UserStatus.basic,
            total_active_days=0,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
    else:
        user.username = username
        user.first_name = first_name
        if lang_code := _resolve_supplied_supported_language(i18n, language_code):
            user.lang_code = lang_code
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


async def ensure_minimal_user(session: AsyncSession, *, tg_id: int, i18n: JsonI18n) -> User:
    user = await get_user_by_tg_id(session, tg_id)
    if user is not None:
        return user

    now = datetime.now(UTC)
    user = User(
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code=resolve_language(i18n, None),
        status=UserStatus.basic,
        total_active_days=0,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    return user
