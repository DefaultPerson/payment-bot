from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import bot.services.message_ttl as ttl


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})


class FakeBot:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))


def test_schedule_message_deletion_adds_a_dated_job(monkeypatch) -> None:
    scheduler = FakeScheduler()
    monkeypatch.setattr(ttl, "_scheduler", scheduler)

    ttl.schedule_message_deletion(111, 222, delay_seconds=300)

    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]
    assert job["func"] is ttl.delete_message_job  # module-level → picklable into Redis
    assert job["args"] == [111, 222]
    assert job["id"] == "msgttl:111:222"
    assert job["replace_existing"] is True
    assert job["misfire_grace_time"] == 3600


def test_schedule_message_deletion_noop_when_ttl_disabled(monkeypatch) -> None:
    scheduler = FakeScheduler()
    monkeypatch.setattr(ttl, "_scheduler", scheduler)

    ttl.schedule_message_deletion(1, 2, delay_seconds=0)

    assert scheduler.jobs == []


def test_schedule_message_deletion_noop_when_scheduler_unset(monkeypatch) -> None:
    monkeypatch.setattr(ttl, "_scheduler", None)

    ttl.schedule_message_deletion(1, 2, delay_seconds=300)  # must not raise


def test_schedule_deletion_extracts_chat_and_message_id(monkeypatch) -> None:
    scheduler = FakeScheduler()
    monkeypatch.setattr(ttl, "_scheduler", scheduler)

    message = SimpleNamespace(chat=SimpleNamespace(id=55), message_id=66)
    ttl.schedule_deletion(message, delay_seconds=120)

    assert scheduler.jobs[0]["args"] == [55, 66]


def test_schedule_deletion_noop_for_missing_message(monkeypatch) -> None:
    scheduler = FakeScheduler()
    monkeypatch.setattr(ttl, "_scheduler", scheduler)

    ttl.schedule_deletion(None, delay_seconds=120)
    ttl.schedule_deletion(SimpleNamespace(chat=None, message_id=None), delay_seconds=120)

    assert scheduler.jobs == []


async def test_delete_message_job_deletes_via_process_bot(monkeypatch) -> None:
    bot = FakeBot()
    monkeypatch.setattr(ttl, "_bot", bot)

    await ttl.delete_message_job(9, 10)

    assert bot.deleted == [(9, 10)]


async def test_delete_message_job_noop_without_bot(monkeypatch) -> None:
    monkeypatch.setattr(ttl, "_bot", None)

    await ttl.delete_message_job(1, 2)  # must not raise


def test_configure_message_ttl_sets_process_globals(monkeypatch) -> None:
    monkeypatch.setattr(ttl, "_bot", None)
    monkeypatch.setattr(ttl, "_scheduler", None)
    bot = FakeBot()
    scheduler = FakeScheduler()

    ttl.configure_message_ttl(bot, scheduler)

    assert ttl._bot is bot
    assert ttl._scheduler is scheduler
