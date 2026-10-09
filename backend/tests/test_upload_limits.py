from __future__ import annotations

import json
import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.middleware.upload_limits import UploadLimitsMiddleware


class UploadLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, middleware, chunks, headers=(), auth=None, delay=0):
        messages = [{"type": "http.request", "body": body, "more_body": i < len(chunks) - 1} for i, body in enumerate(chunks)]
        async def delayed_receive():
            await asyncio.sleep(delay)
            return messages.pop(0)
        receive = AsyncMock(side_effect=delayed_receive if delay else messages)
        sent = []
        async def send(event):
            sent.append(event)
        fake_session = AsyncMock()
        scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": "/api/attachments", "headers": list(headers), "query_string": b"", "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1)}
        authenticate = AsyncMock(return_value=SimpleNamespace(id=1), side_effect=auth)
        with patch("app.api.deps.get_current_user", authenticate), patch("app.db.session.AsyncSessionLocal", return_value=fake_session):
            await middleware(scope, receive, send)
        return sent, receive, authenticate

    def middleware(self):
        received = []
        async def downstream(scope, receive, send):
            received.append(await receive())
            await send({"type": "http.response.start", "status": 201, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return UploadLimitsMiddleware(downstream), received

    async def test_limit_before_downstream_without_content_length(self):
        middleware, received = self.middleware()
        with patch("app.middleware.upload_limits.get_settings", return_value=replace(get_settings(), upload_max_request_bytes=8)):
            sent, receive, _ = await self.run_request(middleware, [b"1234", b"56789", b"unread"])
        self.assertEqual(sent[0]["status"], 413)
        self.assertEqual(receive.await_count, 2)
        self.assertEqual(received, [])
        self.assertEqual(middleware.inflight, 0)

    async def test_forged_content_length_does_not_bypass_limit(self):
        middleware, received = self.middleware()
        with patch("app.middleware.upload_limits.get_settings", return_value=replace(get_settings(), upload_max_request_bytes=8)):
            sent, _, _ = await self.run_request(middleware, [b"123456789"], [(b"content-length", b"1")])
        self.assertEqual(sent[0]["status"], 413)
        self.assertEqual(received, [])

    async def test_large_declared_size_is_rejected_without_reading(self):
        middleware, _ = self.middleware()
        sent, receive, _ = await self.run_request(middleware, [b"must not read"], [(b"content-length", b"999999999")])
        self.assertEqual(sent[0]["status"], 413)
        receive.assert_not_awaited()

    async def test_authentication_precedes_body_and_quota(self):
        middleware, received = self.middleware()
        sent, receive, _ = await self.run_request(middleware, [b"must not read"], auth=AppError(401, "UNAUTHORIZED", "未登录"))
        self.assertEqual(sent[0]["status"], 401)
        receive.assert_not_awaited()
        self.assertEqual(received, [])
        self.assertEqual(len(middleware.attempts), 0)

    async def test_rate_limit_returns_retry_after_and_recovers(self):
        middleware, received = self.middleware()
        with patch("app.middleware.upload_limits.get_settings", return_value=replace(get_settings(), upload_rate_per_minute=1)):
            first, _, _ = await self.run_request(middleware, [b"first"])
            second, receive, _ = await self.run_request(middleware, [b"second"])
        self.assertEqual(first[0]["status"], 201)
        self.assertEqual(second[0]["status"], 429)
        self.assertIn((b"retry-after", b"60"), second[0]["headers"])
        receive.assert_not_awaited()
        self.assertEqual(len(received), 1)

    async def test_boundary_accepted_and_replayed_as_bounded_body(self):
        middleware, received = self.middleware()
        with patch("app.middleware.upload_limits.get_settings", return_value=replace(get_settings(), upload_max_request_bytes=8)):
            sent, _, _ = await self.run_request(middleware, [b"1234", b"5678"])
        self.assertEqual(sent[0]["status"], 201)
        self.assertEqual(received[0], {"type": "http.request", "body": b"12345678", "more_body": False})

    async def test_slow_upload_times_out_and_releases_admission(self):
        middleware, received = self.middleware()
        with patch("app.middleware.upload_limits.get_settings", return_value=replace(get_settings(), upload_body_timeout_seconds=0.001)):
            sent, _, _ = await self.run_request(middleware, [b"slow"], delay=0.02)
        self.assertEqual(sent[0]["status"], 408)
        self.assertEqual(received, [])
        self.assertEqual(middleware.inflight, 0)


if __name__ == "__main__":
    unittest.main()
