from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from bot.core.i18n import JsonI18n
from bot.models import User, UserStatus
from bot.repos.users import (
    ensure_minimal_user,
    get_user_by_tg_id,
    resolve_language,
    update_user_language,
    upsert_user_from_telegram,
    upsert_user_from_telegram_with_created,
)


@dataclass(slots=True)
class FakeExecuteResult:
    inserted_id: int | None

    def scalar_one_or_none(self) -> int | None:
        return self.inserted_id


class FakeAsyncSession:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.flushed = 0
        self.executed_statements: list[Any] = []
        self._next_user_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, User) and instance not in self.users:
            self.users.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for user in self.users:
            if user.id is None:
                user.id = self._next_user_id
                self._next_user_id += 1

    async def execute(self, statement: Any) -> FakeExecuteResult:
        self.executed_statements.append(statement)
        if getattr(getattr(statement, "table", None), "name", None) != User.__tablename__:
            msg = f"unsupported fake execute for {statement}"
            raise AssertionError(msg)

        params = statement.compile(dialect=postgresql.dialect()).params
        if any(user.tg_id == params["tg_id"] for user in self.users):
            return FakeExecuteResult(inserted_id=None)

        inserted_id = self._next_user_id
        self._next_user_id += 1
        self.add(
            User(
                id=inserted_id,
                tg_id=params["tg_id"],
                username=params["username"],
                first_name=params["first_name"],
                lang_code=params["lang_code"],
                status=params["status"],
                total_active_days=params["total_active_days"],
                referrer_id=None,
                referral_percent=None,
                created_at=params["created_at"],
                updated_at=params["updated_at"],
            )
        )
        return FakeExecuteResult(inserted_id=inserted_id)

    async def scalar(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        field = statement.whereclause.left.key
        value = statement.whereclause.right.value

        if entity is User and field == "tg_id":
            return next((user for user in self.users if user.tg_id == value), None)

        msg = f"unsupported fake query for {entity}.{field}"
        raise AssertionError(msg)


def make_user(*, tg_id: int = 1001, lang_code: str = "ru") -> User:
    timestamp = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    return User(
        id=None,
        tg_id=tg_id,
        username=None,
        first_name=None,
        lang_code=lang_code,
        status=UserStatus.basic,
        total_active_days=0,
        referrer_id=None,
        referral_percent=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_resolve_language_maps_telegram_uk_and_regional_tags() -> None:
    i18n = JsonI18n()

    assert resolve_language(i18n, "uk") == "ua"
    assert resolve_language(i18n, "en-US") == "en"
    assert resolve_language(i18n, "de-DE") == "ru"


async def test_get_user_by_tg_id_returns_none_when_absent() -> None:
    session = FakeAsyncSession()

    assert await get_user_by_tg_id(session, 999_999) is None


async def test_upsert_user_from_telegram_creates_user_with_normalized_language() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()

    user = await upsert_user_from_telegram(
        session,
        tg_id=12345,
        username="hydra_user",
        first_name="Hydra",
        language_code="uk",
        i18n=i18n,
    )

    assert user.id == 1
    assert user.tg_id == 12345
    assert user.username == "hydra_user"
    assert user.first_name == "Hydra"
    assert user.lang_code == "ua"
    assert user.status is UserStatus.basic
    assert user.total_active_days == 0
    assert user.created_at.tzinfo is UTC
    assert user.updated_at.tzinfo is UTC
    assert session.flushed == 1


async def test_upsert_user_from_telegram_with_created_reports_inserted_user() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()

    result = await upsert_user_from_telegram_with_created(
        session,
        tg_id=12345,
        username="hydra_user",
        first_name="Hydra",
        language_code="uk",
        i18n=i18n,
    )

    assert result.created is True
    assert result.user.id == 1
    assert result.user.tg_id == 12345
    assert result.user.lang_code == "ua"


async def test_upsert_user_from_telegram_with_created_reports_existing_user_on_conflict() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()
    user = make_user(tg_id=12345, lang_code="en")
    user.username = "old"
    session.add(user)
    await session.flush()

    result = await upsert_user_from_telegram_with_created(
        session,
        tg_id=12345,
        username="new",
        first_name="New",
        language_code="uk",
        i18n=i18n,
    )

    assert result.created is False
    assert result.user is user
    assert result.user.username == "new"
    assert result.user.first_name == "New"
    assert result.user.lang_code == "en"


@pytest.mark.parametrize("language_code", [None, "de-DE"])
async def test_upsert_user_from_telegram_creates_user_with_fallback_language(
    language_code: str | None,
) -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()

    user = await upsert_user_from_telegram(
        session,
        tg_id=12345,
        username="hydra_user",
        first_name="Hydra",
        language_code=language_code,
        i18n=i18n,
    )

    assert user.lang_code == "ru"
    assert session.flushed == 1


async def test_upsert_user_from_telegram_updates_existing_user_without_new_created_at() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()
    user = await upsert_user_from_telegram(
        session,
        tg_id=12345,
        username="old_name",
        first_name="Old",
        language_code="ru",
        i18n=i18n,
    )
    original_created_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    user.created_at = original_created_at
    user.updated_at = original_created_at

    updated = await upsert_user_from_telegram(
        session,
        tg_id=12345,
        username="new_name",
        first_name="New",
        language_code="en-US",
        i18n=i18n,
    )

    assert updated is user
    assert updated.username == "new_name"
    assert updated.first_name == "New"
    assert updated.lang_code == "ru"
    assert updated.created_at == original_created_at
    assert updated.updated_at > original_created_at
    assert len(session.users) == 1
    assert session.flushed == 2


@pytest.mark.parametrize("language_code", [None, "de-DE"])
async def test_upsert_user_from_telegram_keeps_existing_language_for_bad_telegram_locale(
    language_code: str | None,
) -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()
    user = make_user(tg_id=12345, lang_code="ua")
    session.add(user)
    await session.flush()

    updated = await upsert_user_from_telegram(
        session,
        tg_id=user.tg_id,
        username="new_name",
        first_name="New",
        language_code=language_code,
        i18n=i18n,
    )

    assert updated is user
    assert updated.username == "new_name"
    assert updated.first_name == "New"
    assert updated.lang_code == "ua"
    assert session.flushed == 2


async def test_upsert_user_preserves_existing_language_for_telegram_uk_alias() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()
    user = make_user(tg_id=12345, lang_code="ru")
    session.add(user)
    await session.flush()

    updated = await upsert_user_from_telegram(
        session,
        tg_id=user.tg_id,
        username="new_name",
        first_name="New",
        language_code="uk",
        i18n=i18n,
    )

    assert updated is user
    assert updated.lang_code == "ru"
    assert session.flushed == 2


async def test_update_user_language_persists_supported_language() -> None:
    session = FakeAsyncSession()
    user = make_user(lang_code="ru")
    session.add(user)
    await session.flush()

    updated = await update_user_language(session, tg_id=user.tg_id, lang_code="en")

    assert updated is user
    assert updated.lang_code == "en"
    assert updated.updated_at > user.created_at
    assert session.flushed == 2


async def test_update_user_language_rejects_unsupported_language() -> None:
    session = FakeAsyncSession()
    user = make_user(lang_code="ru")
    session.add(user)
    await session.flush()

    with pytest.raises(ValueError, match="unsupported language"):
        await update_user_language(session, tg_id=user.tg_id, lang_code="de")

    assert user.lang_code == "ru"
    assert session.flushed == 1


async def test_update_user_language_rejects_missing_user() -> None:
    session = FakeAsyncSession()

    with pytest.raises(LookupError, match="user with tg_id 404 was not found"):
        await update_user_language(session, tg_id=404, lang_code="ua")


async def test_ensure_minimal_user_creates_missing_user_with_fallback_language() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()

    user = await ensure_minimal_user(session, tg_id=54321, i18n=i18n)

    assert user.id == 1
    assert user.tg_id == 54321
    assert user.username is None
    assert user.first_name is None
    assert user.lang_code == "ru"
    assert user.status is UserStatus.basic
    assert user.total_active_days == 0
    assert session.flushed == 1


async def test_ensure_minimal_user_creates_missing_user_with_telegram_language() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()

    user = await ensure_minimal_user(session, tg_id=54321, i18n=i18n, language_code="uk")

    assert user.lang_code == "ua"


async def test_ensure_minimal_user_uses_postgres_conflict_safe_insert() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()

    await ensure_minimal_user(session, tg_id=54321, i18n=i18n, language_code="en")

    assert len(session.executed_statements) == 1
    compiled = str(session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (tg_id) DO NOTHING" in compiled


async def test_ensure_minimal_user_returns_existing_user_without_overwriting() -> None:
    session = FakeAsyncSession()
    i18n = JsonI18n()
    user = make_user(tg_id=54321, lang_code="ua")
    user.username = "existing"
    session.add(user)
    await session.flush()

    found = await ensure_minimal_user(session, tg_id=54321, i18n=i18n)

    assert found is user
    assert found.username == "existing"
    assert found.lang_code == "ua"
    assert session.flushed == 1
