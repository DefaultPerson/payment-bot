from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.config import Settings
from bot.core.i18n import JsonI18n
from bot.keyboards import CB_BACK_TARIFFS, CB_PROMO, CB_PROMO_BUY_PREFIX, CB_PROMO_CANCEL
from tests.test_handlers_user import (
    FakeCallback,
    FakeChat,
    FakeSession,
    FakeSessionFactory,
    FakeTelegramUser,
)
from tests.test_handlers_user import (
    FakeMessage as CallbackFakeMessage,
)


class FakeState:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(data or {})
        self.state: Any = None
        self.cleared = False

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def update_data(self, **kwargs: Any) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def clear(self) -> None:
        self.cleared = True
        self.state = None
        self._data = {}


@dataclass(slots=True)
class FakePromoRecord:
    code: str


class FakeMessage:
    def __init__(self, *, text: str | None, from_user: FakeTelegramUser | None) -> None:
        self.text = text
        self.from_user = from_user
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append({"text": text, **kwargs})


async def test_promo_command_stores_pending_promo(monkeypatch) -> None:
    import bot.handlers.promo as promo_module

    calls: list[dict[str, Any]] = []

    async def fake_apply_promo_code_for_user(
        session: FakeSession,
        *,
        tg_id: int,
        code: str,
        language_code: str | None,
        i18n: JsonI18n,
    ) -> FakePromoRecord:
        calls.append(
            {
                "session": session,
                "tg_id": tg_id,
                "code": code,
                "language_code": language_code,
                "i18n": i18n,
            }
        )
        return FakePromoRecord(code="SAVE20")

    monkeypatch.setattr(
        promo_module,
        "apply_promo_code_for_user",
        fake_apply_promo_code_for_user,
    )

    i18n = JsonI18n()
    session_factory = FakeSessionFactory()
    message = FakeMessage(
        text="/promo save20",
        from_user=FakeTelegramUser(id=700, language_code="en"),
    )

    await promo_module.promo_command(
        message,
        settings=Settings(),
        i18n=i18n,
        session_factory=session_factory,
    )

    assert calls == [
        {
            "session": session_factory.session,
            "tg_id": 700,
            "code": "save20",
            "language_code": "en",
            "i18n": i18n,
        }
    ]
    assert message.answers == [{"text": "Promo code SAVE20 will be applied to your next invoice."}]


async def test_promo_command_without_code_replies_usage() -> None:
    import bot.handlers.promo as promo_module

    message = FakeMessage(
        text="/promo",
        from_user=FakeTelegramUser(id=700, language_code="en"),
    )

    await promo_module.promo_command(
        message,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert message.answers == [{"text": "Usage: /promo &lt;code&gt;"}]


async def test_promo_callback_prompts_for_command() -> None:
    import bot.handlers.promo as promo_module

    callback = FakeCallback(
        data=CB_PROMO,
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=FakeChat(id=700),
    )

    await promo_module.promo_callback(callback, i18n=JsonI18n())

    assert callback.answers == [
        {"text": "Send /promo &lt;code&gt; to apply a promo code.", "show_alert": True}
    ]


async def test_promo_buy_callback_prompts_and_sets_state() -> None:
    import bot.handlers.promo as promo_module

    state = FakeState()
    message = CallbackFakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data=f"{CB_PROMO_BUY_PREFIX}main",
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=message,
    )

    await promo_module.promo_buy_callback(
        callback,
        state=state,
        settings=Settings(),
        i18n=JsonI18n(),
    )

    assert state.state is promo_module.BuyPromoStates.waiting_code
    assert (await state.get_data())["plan_code"] == "main"
    assert len(message.edits) == 1
    assert message.edits[0]["text"] == "Send your promo code as a message:"
    assert message.edits[0]["reply_markup"].inline_keyboard[0][0].callback_data == CB_PROMO_CANCEL
    assert callback.answers == [{"text": None}]


async def test_promo_code_message_applies_and_rerenders_duration_menu(monkeypatch) -> None:
    import bot.handlers.promo as promo_module

    async def fake_apply(session: FakeSession, **kwargs: Any) -> FakePromoRecord:
        return FakePromoRecord(code="SAVE20")

    monkeypatch.setattr(promo_module, "apply_promo_code_for_user", fake_apply)

    state = FakeState({"plan_code": "main"})
    message = FakeMessage(text="save20", from_user=FakeTelegramUser(id=700, language_code="en"))

    await promo_module.promo_code_message(
        message,
        state=state,
        settings=Settings(),
        i18n=JsonI18n(),
        session_factory=FakeSessionFactory(),
    )

    assert state.cleared is True
    answer = message.answers[0]
    assert "Promo code SAVE20 will be applied to your next invoice." in answer["text"]
    assert "Choose a duration:" in answer["text"]
    assert CB_BACK_TARIFFS in {
        button.callback_data
        for row in answer["reply_markup"].inline_keyboard
        for button in row
        if button.callback_data is not None
    }


async def test_promo_cancel_callback_returns_to_duration_menu() -> None:
    import bot.handlers.promo as promo_module

    state = FakeState({"plan_code": "main"})
    message = CallbackFakeMessage(chat=FakeChat(id=700))
    callback = FakeCallback(
        data=CB_PROMO_CANCEL,
        from_user=FakeTelegramUser(id=700, language_code="en"),
        message=message,
    )

    await promo_module.promo_cancel_callback(
        callback,
        state=state,
        settings=Settings(),
        i18n=JsonI18n(),
    )

    assert state.cleared is True
    assert message.edits[0]["text"] == "Choose a duration:"
