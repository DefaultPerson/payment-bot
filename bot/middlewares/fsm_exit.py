"""Let a bot command always escape a half-finished FSM flow.

While an admin sits inside a broadcast composer (``BroadcastUIState``) or an admin-panel flow
(``Admin*State``), state-gated handlers capture/delete arbitrary messages — including ``/``
commands, which would then never run. This clears the FSM state when a command arrives, so the
command is handled normally and the admin leaves the flow (trade-off: a captured message cannot
itself start with ``/``). Register on ``dispatcher.message.outer_middleware``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class FsmCommandExitMiddleware(BaseMiddleware):
    def __init__(self, prefixes: tuple[str, ...]) -> None:
        self._prefixes = prefixes

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state = data.get("state")
        text = getattr(event, "text", None)
        if state is not None and text and text.startswith("/"):
            current = await state.get_state()
            if current and current.startswith(self._prefixes):
                await state.set_state(None)
        return await handler(event, data)
