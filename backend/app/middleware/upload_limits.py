from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import AppError, error_payload


class UploadLimitsMiddleware:
    """Authenticate and bound the complete multipart body before parsing starts."""
    def __init__(self, app):
        self.app = app
        self.attempts = defaultdict(deque)
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") != "/api/attachments":
            await self.app(scope, receive, send)
            return
        from app.api.deps import get_current_user
        from app.db.session import AsyncSessionLocal
        settings = get_settings()
        admitted = False
        try:
            async with AsyncSessionLocal() as session:
                user = await get_current_user(Request(scope), session)
                user_id = user.id
            now = time.monotonic()
            history = self.attempts[user_id]
            while history and history[0] <= now - 60:
                history.popleft()
            if len(history) >= settings.upload_rate_per_minute or self.inflight >= max(2, settings.image_decode_concurrency + 1):
                raise AppError(429, "UPLOAD_RATE_LIMIT", "图片上传繁忙，请稍后重试")
            history.append(now)
            self.inflight += 1
            admitted = True
            declared = dict(scope.get("headers", [])).get(b"content-length")
            if declared:
                try:
                    length = int(declared)
                except ValueError:
                    raise AppError(400, "INVALID_REQUEST", "请求长度无效")
                if length < 0:
                    raise AppError(400, "INVALID_REQUEST", "请求长度无效")
                if length > settings.upload_max_request_bytes:
                    raise AppError(413, "UPLOAD_TOO_LARGE", "图片上传请求过大")
            chunks = bytearray()
            try:
                async with asyncio.timeout(settings.upload_body_timeout_seconds):
                    while True:
                        event = await receive()
                        if event["type"] == "http.disconnect":
                            return
                        body = event.get("body", b"")
                        if len(chunks) + len(body) > settings.upload_max_request_bytes:
                            raise AppError(413, "UPLOAD_TOO_LARGE", "图片上传请求过大")
                        chunks.extend(body)
                        if not event.get("more_body", False):
                            break
            except TimeoutError as exc:
                raise AppError(408, "UPLOAD_TIMEOUT", "图片上传超时，请重试") from exc
            replayed = False
            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": bytes(chunks), "more_body": False}
                return await receive()
            await self.app(scope, replay, send)
        except AppError as exc:
            response = JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, exc.details), headers={"Retry-After": "60"} if exc.status_code == 429 else None)
            await response(scope, receive, send)
        finally:
            if admitted:
                self.inflight -= 1
