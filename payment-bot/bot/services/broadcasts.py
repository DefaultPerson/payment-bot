from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from aiogram_broadcast import BaseBroadcastStorage, BroadcastResult, BroadcastService, Subscriber
from aiogram_broadcast.storage.base import SubscriberState

from bot.repos.broadcasts import BroadcastTarget


class SupportsBroadcastBot(Protocol):
    async def send_message(self, **kwargs: Any) -> object: ...


async def run_text_broadcast(
    bot: SupportsBroadcastBot,
    *,
    targets: list[BroadcastTarget],
    text: str,
    rate_limit: float = 0.05,
    progress_callback: Callable[[int, int, BroadcastResult], Awaitable[None]] | None = None,
) -> BroadcastResult:
    storage = _TargetBroadcastStorage(targets)
    service = BroadcastService(bot, storage, rate_limit=rate_limit)

    async def sender(chat_id: int) -> None:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=None)

    return await service.broadcast_custom(
        sender,
        only_active=True,
        progress_callback=progress_callback,
    )


class _TargetBroadcastStorage(BaseBroadcastStorage):
    def __init__(self, targets: list[BroadcastTarget]) -> None:
        self._subscribers = {
            target.tg_id: Subscriber(
                id=target.tg_id,
                full_name=str(target.tg_id),
                username=None,
                language_code=target.lang_code,
            )
            for target in targets
        }

    async def add_subscriber(self, subscriber: Subscriber) -> None:
        self._subscribers[subscriber.id] = subscriber

    async def get_subscriber(self, user_id: int) -> Subscriber | None:
        return self._subscribers.get(user_id)

    async def update_subscriber(self, subscriber: Subscriber) -> None:
        self._subscribers[subscriber.id] = subscriber

    async def delete_subscriber(self, user_id: int) -> bool:
        return self._subscribers.pop(user_id, None) is not None

    async def get_all_subscriber_ids(
        self,
        state: SubscriberState | None = None,
    ) -> list[int]:
        return [
            subscriber.id
            for subscriber in self._subscribers.values()
            if state is None or subscriber.state is state
        ]

    async def get_subscribers_count(
        self,
        state: SubscriberState | None = None,
    ) -> int:
        return len(await self.get_all_subscriber_ids(state=state))

    async def iter_subscribers(
        self,
        state: SubscriberState | None = None,
        batch_size: int = 100,
    ) -> AsyncIterator[Subscriber]:
        del batch_size
        for subscriber in self._subscribers.values():
            if state is None or subscriber.state is state:
                yield subscriber
