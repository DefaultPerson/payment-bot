from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bot.handlers.admin.panel as panel
from bot.config import Settings
from bot.core.i18n import JsonI18n


@dataclass(slots=True)
class FakeUser:
    id: int
    language_code: str | None = "en"


class FakeMessage:
    def __init__(self) -> None:
        self.edited: dict[str, Any] | None = None
        self.deleted = False

    async def edit_text(self, text: str, reply_markup: Any = None) -> None:
        self.edited = {"text": text, "reply_markup": reply_markup}

    async def delete(self) -> None:
        self.deleted = True


class FakeCallback:
    def __init__(self, from_user: FakeUser | None, message: FakeMessage | None) -> None:
        self.from_user = from_user
        self.message = message
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


def _callback_datas(markup: Any) -> set[str]:
    return {b.callback_data for row in markup.inline_keyboard for b in row}


async def test_open_panel_renders_root_and_clears_state() -> None:
    message = FakeMessage()
    callback = FakeCallback(FakeUser(500), message)

    class State:
        current = "AdminPromoState:code"

        async def set_state(self, value: Any) -> None:
            self.current = value

    state = State()
    await panel.open_panel(
        callback, settings=Settings(admin_ids=[500]), i18n=JsonI18n(), state=state
    )

    assert message.edited is not None
    assert message.edited["text"] == "Admin panel. Choose an action."
    datas = _callback_datas(message.edited["reply_markup"])
    assert {"adm:grant", "adm:refset", "adm:promo", "adm:bcast", "adm:stats", "adm:close"} <= datas
    assert state.current is None
    assert callback.answers == [{"text": None}]


async def test_open_panel_non_admin_is_noop() -> None:
    message = FakeMessage()
    callback = FakeCallback(FakeUser(999), message)

    class State:
        async def set_state(self, value: Any) -> None:  # pragma: no cover - must not run
            raise AssertionError("non-admin must not mutate state")

    await panel.open_panel(
        callback, settings=Settings(admin_ids=[500]), i18n=JsonI18n(), state=State()
    )

    assert message.edited is None
    assert callback.answers == [{"text": None}]


async def test_close_panel_deletes_message_and_clears_state() -> None:
    message = FakeMessage()
    callback = FakeCallback(FakeUser(500), message)

    class State:
        current = "X"

        async def set_state(self, value: Any) -> None:
            self.current = value

    state = State()
    await panel.close_panel(
        callback, settings=Settings(admin_ids=[500]), i18n=JsonI18n(), state=state
    )

    assert message.deleted is True
    assert state.current is None
    assert callback.answers == [{"text": None}]


async def test_noop_just_answers() -> None:
    callback = FakeCallback(FakeUser(500), FakeMessage())
    await panel.noop(callback)
    assert callback.answers == [{"text": None}]
