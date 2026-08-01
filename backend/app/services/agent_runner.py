from __future__ import annotations

import asyncio
from collections.abc import Awaitable


class InProcessAgentRunner:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def start(self, run_id: int, job: Awaitable[None]) -> None:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(job)
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    def is_running(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def shutdown(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if not tasks:
            self._tasks.clear()
            return

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


agent_runner = InProcessAgentRunner()
