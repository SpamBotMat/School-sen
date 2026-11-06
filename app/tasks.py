"""Фоновые задачи с сильной ссылкой (чтобы их не съел сборщик мусора) и логированием ошибок."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


def background(coro, name: str | None = None) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _tasks.add(task)

    def done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("Фоновая задача %s упала", t.get_name(), exc_info=t.exception())

    task.add_done_callback(done)
    return task


async def drain(timeout: float = 10) -> None:
    if _tasks:
        await asyncio.wait(list(_tasks), timeout=timeout)


def pending() -> int:
    return len(_tasks)
