from __future__ import annotations

import asyncio
from collections.abc import Awaitable


class InProcessAgentRunner:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._cancelling: set[int] = set()
        self._ready: set[int] = set()

    def start(self, run_id: int, job: Awaitable[None]) -> None:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            if hasattr(job, "close"):
                job.close()
            return

        task = asyncio.create_task(job)
        self._tasks[run_id] = task
        def finished(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(run_id) is completed:
                self._tasks.pop(run_id, None)
            self._cancelling.discard(run_id)
            self._ready.discard(run_id)
            if not completed.cancelled():
                completed.exception()  # Retrieve failures; persisted run recovery handles interruption.

        task.add_done_callback(finished)

    async def wait(self, run_id: int) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)

    def execution_ready(self, run_id: int) -> bool:
        self._ready.add(run_id)
        return run_id not in self._cancelling

    def execution_stopping(self, run_id: int) -> None:
        self._ready.discard(run_id)

    def is_running(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def cancel(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False

        # The durable cancel flag is written before this notification. A task
        # cancelled before its first instruction cannot execute its cleanup.
        if run_id not in self._cancelling:
            self._cancelling.add(run_id)
            if run_id in self._ready:
                task.cancel()
        return True

    async def shutdown(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if not tasks:
            self._tasks.clear()
            return

        for run_id in list(self._tasks):
            await self.cancel(run_id)
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


agent_runner = InProcessAgentRunner()
