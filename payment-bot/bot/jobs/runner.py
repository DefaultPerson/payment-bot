from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ManagedTask:
    name: str
    task: asyncio.Task[Any]

    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()

    async def wait(self) -> None:
        try:
            await self.task
        except asyncio.CancelledError:
            logger.debug("managed task cancelled", extra={"task_name": self.name})
        except Exception:
            logger.exception("managed task failed", extra={"task_name": self.name})


class TaskRunner:
    def __init__(self) -> None:
        self._tasks: list[ManagedTask] = []

    @property
    def tasks(self) -> tuple[ManagedTask, ...]:
        return tuple(self._tasks)

    def start(self, name: str, coroutine: Coroutine[Any, Any, Any]) -> ManagedTask:
        managed_task = ManagedTask(name=name, task=asyncio.create_task(coroutine, name=name))
        self._tasks.append(managed_task)
        return managed_task

    async def stop(self) -> None:
        for managed_task in self._tasks:
            managed_task.cancel()

        for managed_task in self._tasks:
            await managed_task.wait()

        self._tasks.clear()
