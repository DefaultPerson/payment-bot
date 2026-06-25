from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.repos.broadcasts import BroadcastTarget
from bot.services.broadcasts import run_text_broadcast


class FakeBot:
    def __init__(self, *, fail_chat_id: int | None = None) -> None:
        self.fail_chat_id = fail_chat_id
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        if kwargs["chat_id"] == self.fail_chat_id:
            raise RuntimeError("send failed")
        self.messages.append(kwargs)


@dataclass(frozen=True, slots=True)
class ProgressCall:
    current: int
    total: int
    successful: int
    failed: int


async def test_run_text_broadcast_sends_plain_text_to_targets() -> None:
    bot = FakeBot()
    targets = [
        BroadcastTarget(tg_id=1001, lang_code="en"),
        BroadcastTarget(tg_id=1002, lang_code="ru"),
    ]
    progress: list[ProgressCall] = []

    async def on_progress(current: int, total: int, result: Any) -> None:
        progress.append(
            ProgressCall(
                current=current,
                total=total,
                successful=result.successful,
                failed=result.failed,
            )
        )

    result = await run_text_broadcast(
        bot,
        targets=targets,
        text="price < 10",
        rate_limit=0,
        progress_callback=on_progress,
    )

    assert result.total == 2
    assert result.successful == 2
    assert result.failed == 0
    assert bot.messages == [
        {"chat_id": 1001, "text": "price < 10", "parse_mode": None},
        {"chat_id": 1002, "text": "price < 10", "parse_mode": None},
    ]


async def test_run_text_broadcast_counts_failed_sends() -> None:
    bot = FakeBot(fail_chat_id=1002)
    targets = [
        BroadcastTarget(tg_id=1001, lang_code="en"),
        BroadcastTarget(tg_id=1002, lang_code="ru"),
    ]

    result = await run_text_broadcast(bot, targets=targets, text="Hello", rate_limit=0)

    assert result.total == 2
    assert result.successful == 1
    assert result.failed == 1
    assert result.errors[1002] == "send failed"


async def test_run_text_broadcast_handles_empty_targets() -> None:
    bot = FakeBot()

    result = await run_text_broadcast(bot, targets=[], text="Hello", rate_limit=0)

    assert result.total == 0
    assert result.successful == 0
    assert result.failed == 0
    assert bot.messages == []
