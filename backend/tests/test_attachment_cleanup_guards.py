from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.services.attachment_cleanup import _advisory_guard


class CleanupGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_and_connection_bind_use_async_engine(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        connection_bind = await engine.connect()
        self.assertIsInstance(connection_bind, AsyncConnection)
        for bind in (engine, connection_bind):
            dedicated = AsyncMock()
            dedicated.scalar.return_value = 1
            connect = AsyncMock(return_value=dedicated)
            with patch.object(engine.sync_engine.dialect, "name", "mysql"), patch.object(AsyncEngine, "connect", connect):
                async with _advisory_guard(SimpleNamespace(bind=bind), "guard_test", asyncio.Lock()) as acquired:
                    self.assertTrue(acquired)
                connect.assert_awaited_once()
            dedicated.scalar.assert_awaited_once()
            dedicated.execute.assert_awaited_once()
            self.assertIn("RELEASE_LOCK", str(dedicated.execute.await_args.args[0]))
            dedicated.close.assert_awaited_once()
        await connection_bind.close()
        await engine.dispose()

    async def test_cancellation_releases_lock_on_same_connection(self):
        dedicated = AsyncMock()
        dedicated.scalar.return_value = 1
        bind = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), connect=AsyncMock(return_value=dedicated))
        with self.assertRaises(asyncio.CancelledError):
            async with _advisory_guard(SimpleNamespace(bind=bind), "guard_test", asyncio.Lock()):
                raise asyncio.CancelledError()
        dedicated.execute.assert_awaited_once()
        dedicated.close.assert_awaited_once()

    async def test_repeated_cancellation_waits_for_lock_release_before_pool_return(self):
        dedicated = AsyncMock()
        dedicated.scalar.return_value = 1
        began, finish = asyncio.Event(), asyncio.Event()
        async def blocked_release(*_):
            began.set()
            await finish.wait()
        dedicated.execute.side_effect = blocked_release
        bind = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), connect=AsyncMock(return_value=dedicated))
        async def work():
            async with _advisory_guard(SimpleNamespace(bind=bind), "guard_test", asyncio.Lock()):
                pass
        task = asyncio.create_task(work())
        await began.wait()
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            dedicated.close.assert_not_awaited()
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        dedicated.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
