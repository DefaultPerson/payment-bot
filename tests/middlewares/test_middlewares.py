from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import bot.middlewares.activity as activity_mod
from bot.middlewares import ActivityMiddleware, FsmCommandExitMiddleware

PREFIXES = ("BroadcastUIState", "AdminGrantState", "AdminRefsetState", "AdminPromoState")


# --- FsmCommandExitMiddleware ---------------------------------------------------------------


class _FakeFsmState:
    def __init__(self, current: str | None) -> None:
        self._current = current
        self.cleared = False

    async def get_state(self) -> str | None:
        return self._current

    async def set_state(self, value: Any) -> None:
        assert value is None
        self._current = value
        self.cleared = True


async def _passthrough(event: Any, data: dict[str, Any]) -> str:
    return "handled"


async def test_exit_clears_broadcast_state() -> None:
    state = _FakeFsmState("BroadcastUIState:broadcasts_list")
    mw = FsmCommandExitMiddleware(PREFIXES)

    result = await mw(_passthrough, SimpleNamespace(text="/start"), {"state": state})

    assert state.cleared is True
    assert result == "handled"


async def test_exit_clears_admin_promo_state() -> None:
    state = _FakeFsmState("AdminPromoState:code")
    mw = FsmCommandExitMiddleware(PREFIXES)

    await mw(_passthrough, SimpleNamespace(text="/start"), {"state": state})

    assert state.cleared is True


async def test_exit_keeps_non_command_in_flow() -> None:
    state = _FakeFsmState("AdminGrantState:waiting_input")
    mw = FsmCommandExitMiddleware(PREFIXES)

    await mw(_passthrough, SimpleNamespace(text="123 30"), {"state": state})

    assert state.cleared is False


async def test_exit_ignores_unrelated_state() -> None:
    state = _FakeFsmState("OtherStates:waiting")
    mw = FsmCommandExitMiddleware(PREFIXES)

    await mw(_passthrough, SimpleNamespace(text="/start"), {"state": state})

    assert state.cleared is False


async def test_exit_passthrough_without_state() -> None:
    mw = FsmCommandExitMiddleware(PREFIXES)

    result = await mw(_passthrough, SimpleNamespace(text="/start"), {})

    assert result == "handled"


# --- ActivityMiddleware ---------------------------------------------------------------------


class _FakeRedis:
    def __init__(self, *, acquire: bool) -> None:
        self._acquire = acquire
        self.calls: list[tuple] = []

    async def set(self, key: str, value: Any, *, nx: bool = False, ex: int | None = None):
        self.calls.append((key, value, nx, ex))
        return True if self._acquire else None


async def _handler(event: Any, data: dict[str, Any]) -> str:
    return "ok"


def _patch_db(monkeypatch, bumped: list[int]) -> None:
    async def fake_touch(session: Any, *, tg_id: int, now: Any) -> None:
        bumped.append(tg_id)

    @asynccontextmanager
    async def fake_scope(_factory: Any):
        yield object()

    monkeypatch.setattr(activity_mod, "touch_user_last_active", fake_touch)
    monkeypatch.setattr(activity_mod, "session_scope", fake_scope)


async def test_activity_bumps_when_lock_acquired(monkeypatch) -> None:
    bumped: list[int] = []
    _patch_db(monkeypatch, bumped)
    redis = _FakeRedis(acquire=True)
    mw = ActivityMiddleware(object(), redis)

    result = await mw(_handler, SimpleNamespace(), {"event_from_user": SimpleNamespace(id=42)})

    assert result == "ok"
    assert bumped == [42]
    assert redis.calls[0][0] == "active:bump:42"
    assert redis.calls[0][2] is True  # nx


async def test_activity_skips_when_throttled(monkeypatch) -> None:
    bumped: list[int] = []
    _patch_db(monkeypatch, bumped)
    redis = _FakeRedis(acquire=False)
    mw = ActivityMiddleware(object(), redis)

    result = await mw(_handler, SimpleNamespace(), {"event_from_user": SimpleNamespace(id=7)})

    assert result == "ok"
    assert bumped == []


async def test_activity_skips_without_user(monkeypatch) -> None:
    bumped: list[int] = []
    _patch_db(monkeypatch, bumped)
    redis = _FakeRedis(acquire=True)
    mw = ActivityMiddleware(object(), redis)

    result = await mw(_handler, SimpleNamespace(), {})

    assert result == "ok"
    assert redis.calls == []
    assert bumped == []


async def test_activity_suppresses_errors() -> None:
    class _BoomRedis:
        async def set(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")

    mw = ActivityMiddleware(object(), _BoomRedis())

    result = await mw(_handler, SimpleNamespace(), {"event_from_user": SimpleNamespace(id=1)})

    assert result == "ok"
