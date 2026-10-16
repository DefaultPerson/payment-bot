from __future__ import annotations

import pickle
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram_broadcast import BroadcastScheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import bot.services.broadcast_ui as bui


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append((chat_id, text))


def _state(user_id: int = 500) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id),
    )


def test_create_redis_jobstore_uses_url_db() -> None:
    store = bui.create_redis_jobstore("redis://localhost:6379/4")
    kwargs = store.redis.connection_pool.connection_kwargs
    assert kwargs["db"] == 4
    assert kwargs["host"] == "localhost"


async def test_run_scheduled_broadcast_sends_copies_and_notifies(monkeypatch) -> None:
    sent: list[int] = []

    async def fake_copy(bot: Any, chat_id: int, message_data: dict[str, Any]) -> bool:
        sent.append(chat_id)
        return True

    bot = FakeBot()
    monkeypatch.setattr(bui, "send_message_copy", fake_copy)
    monkeypatch.setattr(bui, "_bot", bot)

    await bui.run_scheduled_broadcast([1001, 1002], {"text": "hi"}, chat_id=500, language="en")

    assert sent == [1001, 1002]
    assert len(bot.messages) == 1
    chat_id, text = bot.messages[0]
    assert chat_id == 500
    assert "2/2" in text


async def test_run_scheduled_broadcast_without_bot_is_noop(monkeypatch) -> None:
    called = False

    async def fake_copy(*args: Any, **kwargs: Any) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(bui, "_bot", None)
    monkeypatch.setattr(bui, "send_message_copy", fake_copy)

    await bui.run_scheduled_broadcast([1], {"text": "x"}, chat_id=1)

    assert called is False


async def test_schedule_broadcast_enqueues_picklable_module_job() -> None:
    state = _state()
    scheduler = AsyncIOScheduler()
    broadcast_scheduler = BroadcastScheduler(SimpleNamespace(), scheduler)
    manager = bui.PersistentBroadcastUIManager(
        bot=SimpleNamespace(),
        user=SimpleNamespace(id=500, language_code="en"),
        state=state,
        service=SimpleNamespace(),
        scheduler=broadcast_scheduler,
    )

    await state.update_data(subscriber_ids=[1001, 1002])
    await manager._data_storage.set("message_data", {"text": "hi"})
    await manager._data_storage.set("scheduled_datetime", datetime.now() + timedelta(hours=1))

    task_id = await manager.schedule_broadcast()

    assert task_id is not None and task_id.startswith("broadcast_")
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    # Module-level callable (not a bound method) so it survives serialization into Redis.
    assert job.func is bui.run_scheduled_broadcast
    assert job.kwargs["subscriber_ids"] == [1001, 1002]
    assert job.kwargs["chat_id"] == 500
    assert job.kwargs["language"] == "en"
    pickle.dumps(job.kwargs)  # persistable -> not lost on restart


async def test_schedule_broadcast_without_scheduler_returns_none() -> None:
    manager = bui.PersistentBroadcastUIManager(
        bot=SimpleNamespace(),
        user=SimpleNamespace(id=1, language_code="en"),
        state=_state(1),
        service=SimpleNamespace(),
        scheduler=None,
    )

    assert await manager.schedule_broadcast() is None
