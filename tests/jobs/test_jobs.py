import asyncio

import pytest

from bot.jobs.runner import TaskRunner


@pytest.mark.asyncio
async def test_task_runner_cancels_and_awaits_started_task_on_stop() -> None:
    started = asyncio.Event()
    cancelled_and_cleaned_up = asyncio.Event()

    async def worker() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        finally:
            cancelled_and_cleaned_up.set()

    runner = TaskRunner()
    runner.start("worker", worker())
    await started.wait()

    await runner.stop()

    assert cancelled_and_cleaned_up.is_set()
