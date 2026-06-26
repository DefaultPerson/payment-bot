from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bot.config import Settings
from bot.models import Tariff
from bot.repos.tariffs import (
    default_plan,
    ensure_configured_tariff,
    get_active_plans,
    get_active_tariffs,
    get_plan,
    get_plan_tariffs,
    get_tariff,
    plan_for_tariff,
    resolve_plan_code,
)


def _multi_plan_settings() -> Settings:
    return Settings(
        tariffs={
            "plans": [
                {"code": "main", "title": "Main", "chat_ids": [-100]},
                {"code": "pro", "title": "Pro", "chat_ids": [-200], "active": False},
            ],
            "items": [
                {
                    "code": "main_1m",
                    "plan_code": "main",
                    "duration_months": 1,
                    "base_price_usd": Decimal("59"),
                },
                {
                    "code": "pro_1m",
                    "plan_code": "pro",
                    "duration_months": 1,
                    "base_price_usd": Decimal("99"),
                },
            ],
        }
    )


def test_get_active_plans_filters_inactive() -> None:
    assert [plan.code for plan in get_active_plans(_multi_plan_settings())] == ["main"]


def test_default_plan_is_first_active() -> None:
    assert default_plan(_multi_plan_settings()).code == "main"


def test_get_plan_resolves_inactive_plans_too() -> None:
    assert get_plan(_multi_plan_settings(), "pro").chat_ids == [-200]


def test_get_plan_tariffs_filters_by_plan() -> None:
    settings = _multi_plan_settings()
    assert [tariff.code for tariff in get_plan_tariffs(settings, "main")] == ["main_1m"]


def test_plan_for_tariff_and_resolve_plan_code() -> None:
    settings = _multi_plan_settings()
    assert plan_for_tariff(settings, "main_1m").code == "main"
    assert resolve_plan_code(settings, "main_1m") == "main"
    assert resolve_plan_code(settings, "unknown") is None
    assert resolve_plan_code(settings, None) is None


class FakeAsyncSession:
    def __init__(self) -> None:
        self.tariffs: list[Tariff] = []
        self.flushed = 0
        self.committed = 0
        self.scalar_queries: list[tuple[type, str, Any]] = []
        self._next_tariff_id = 1

    def add(self, instance: Any) -> None:
        if isinstance(instance, Tariff) and instance not in self.tariffs:
            self.tariffs.append(instance)

    async def flush(self) -> None:
        self.flushed += 1
        for tariff in self.tariffs:
            if tariff.id is None:
                tariff.id = self._next_tariff_id
                self._next_tariff_id += 1

    async def commit(self) -> None:
        self.committed += 1

    async def scalar(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0]["entity"]
        field = statement.whereclause.left.key
        value = statement.whereclause.right.value
        self.scalar_queries.append((entity, field, value))

        if entity is Tariff and field == "code":
            return next((tariff for tariff in self.tariffs if tariff.code == value), None)

        msg = f"unsupported fake query for {entity}.{field}"
        raise AssertionError(msg)


def test_get_active_tariffs_filters_inactive_config_tariffs() -> None:
    settings = Settings(
        tariffs={
            "items": [
                {
                    "code": "active",
                    "duration_months": 1,
                    "base_price_usd": Decimal("79"),
                    "active": True,
                },
                {
                    "code": "inactive",
                    "duration_months": 3,
                    "base_price_usd": Decimal("237"),
                    "active": False,
                },
            ],
        },
    )

    active = get_active_tariffs(settings)

    assert [tariff.code for tariff in active] == ["active"]


def test_get_tariff_resolves_only_active_config_tariffs() -> None:
    settings = Settings(
        tariffs={
            "items": [
                {
                    "code": "active",
                    "duration_months": 1,
                    "base_price_usd": Decimal("79"),
                    "active": True,
                },
                {
                    "code": "inactive",
                    "duration_months": 3,
                    "base_price_usd": Decimal("237"),
                    "active": False,
                },
            ],
        },
    )

    assert get_tariff(settings, "active").code == "active"
    with pytest.raises(LookupError, match="tariff inactive was not found"):
        get_tariff(settings, "inactive")


async def test_ensure_configured_tariff_creates_missing_tariff() -> None:
    session = FakeAsyncSession()

    tariff = await ensure_configured_tariff(session, Settings(), "1m")

    assert tariff.id == 1
    assert tariff.code == "1m"
    assert tariff.duration_months == 1
    assert tariff.base_price_usd == Decimal("59.00")
    assert tariff.active is True
    assert session.flushed == 1
    assert session.committed == 0
    assert session.scalar_queries == [(Tariff, "code", "1m")]


async def test_ensure_configured_tariff_updates_existing_tariff_values() -> None:
    session = FakeAsyncSession()
    existing = Tariff(
        id=7,
        code="1m",
        duration_months=99,
        base_price_usd=Decimal("1.00"),
        active=False,
    )
    session.add(existing)

    tariff = await ensure_configured_tariff(session, Settings(), "1m")

    assert tariff is existing
    assert tariff.id == 7
    assert tariff.duration_months == 1
    assert tariff.base_price_usd == Decimal("59.00")
    assert tariff.active is True
    assert session.flushed == 1
    assert session.committed == 0


@pytest.mark.parametrize(
    ("settings", "code"),
    [
        (Settings(), "unknown"),
        (
            Settings(
                tariffs={
                    "items": [
                        {
                            "code": "inactive",
                            "duration_months": 1,
                            "base_price_usd": Decimal("79"),
                            "active": False,
                        },
                    ],
                },
            ),
            "inactive",
        ),
    ],
)
async def test_ensure_configured_tariff_rejects_unknown_or_inactive_tariff(
    settings: Settings,
    code: str,
) -> None:
    session = FakeAsyncSession()

    with pytest.raises(LookupError, match=f"tariff {code} was not found"):
        await ensure_configured_tariff(session, settings, code)

    assert session.flushed == 0
    assert session.committed == 0
