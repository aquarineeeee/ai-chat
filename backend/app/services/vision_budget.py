from __future__ import annotations

import asyncio
import base64
import inspect
import json
import threading
from dataclasses import dataclass
from contextlib import asynccontextmanager
from functools import wraps
from typing import Protocol

from app.canonical_transcript import CanonicalTranscriptItem, ImagePart, TextPart, user_parts
from app.core.config import get_settings
from app.core.exceptions import AppError


@dataclass(frozen=True, slots=True)
class AttachmentMetadata:
    id: int
    media_type: str
    size_bytes: int
    status: str = "ready"
    width: int = 0
    height: int = 0


class AttachmentReader(Protocol):
    async def metadata(self, attachment_id: int) -> AttachmentMetadata: ...
    async def read(self, attachment_id: int) -> bytes: ...


class VisionBufferBudget:
    """Nonblocking process-wide admission; leases survive tool approvals."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.used_bytes = 0
        self._lock = threading.Lock()

    def reserve(self, amount: int) -> "VisionBufferLease":
        lease = VisionBufferLease(self)
        lease.ensure(amount)
        return lease


class VisionBufferLease:
    def __init__(self, budget: VisionBufferBudget) -> None:
        self.budget = budget
        self.amount = 0
        self.closed = False

    def ensure(self, amount: int) -> None:
        with self.budget._lock:
            if self.closed:
                raise RuntimeError("Vision buffer lease has been released")
            extra = max(0, amount - self.amount)
            if self.budget.used_bytes + extra > self.budget.max_bytes:
                raise AppError(429, "VISION_CAPACITY_EXCEEDED", "图片处理内存额度已满，请稍后重试")
            self.budget.used_bytes += extra
            self.amount += extra

    def close(self) -> None:
        with self.budget._lock:
            if not self.closed:
                self.budget.used_bytes -= self.amount
                self.amount = 0
                self.closed = True


_buffer_budget: VisionBufferBudget | None = None
_encoding_active = 0
_encoding_lock = threading.Lock()


def get_vision_buffer_budget() -> VisionBufferBudget:
    global _buffer_budget
    if _buffer_budget is None:
        _buffer_budget = VisionBufferBudget(getattr(get_settings(), "vision_buffer_max_bytes", 256 * 1024 * 1024))
    return _buffer_budget


def _json_upper_bound(value: object) -> int:
    """Bound serialization without allocating a JSON or UTF-8 copy."""
    if isinstance(value, str):
        # ASCII image strings need one byte; non-ASCII and controls may escape.
        if value.isascii() and value.isprintable():
            return 2 + len(value) + value.count('"') + value.count('\\')
        return 2 + sum(6 if ord(c) < 32 or ord(c) > 126 else 2 if c in '\\"' else 1 for c in value)
    if isinstance(value, dict):
        return 2 + sum(_json_upper_bound(str(k)) + _json_upper_bound(v) + 4 for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return 2 + sum(_json_upper_bound(v) + 2 for v in value)
    return 64


def request_max_bytes(adapter_id: str) -> int:
    field = {
        "openai_chat_completions": "openai_chat_request_max_bytes",
        "openai_responses": "openai_responses_request_max_bytes",
        "anthropic_messages": "anthropic_request_max_bytes",
    }[adapter_id]
    return getattr(get_settings(), field, 16 * 1024 * 1024)


def adapter_image_limits(adapter_id: str) -> tuple[int, int]:
    prefix, default_count, default_dimension = {
        "openai_chat_completions": ("openai_chat", 500, 0),
        "openai_responses": ("openai_responses", 500, 0),
        "anthropic_messages": ("anthropic", 100, 8000),
    }[adapter_id]
    settings = get_settings()
    return (getattr(settings, f"{prefix}_max_images", default_count),
            getattr(settings, f"{prefix}_max_image_dimension", default_dimension))


@asynccontextmanager
async def provider_timeout():
    """A total request deadline also covers streams that trickle forever."""
    try:
        async with asyncio.timeout(getattr(get_settings(), "vision_request_timeout_seconds", 90.0)):
            yield
    except TimeoutError as exc:
        raise AppError(504, "MODEL_TIMEOUT", "模型请求超时，请稍后重试") from exc


class VisionRequestContext:
    def __init__(self, adapter_id: str, reader: AttachmentReader | None) -> None:
        self.adapter_id = adapter_id
        self.reader = reader
        self.images: dict[int, str] = {}
        self.lease: VisionBufferLease | None = None
        self.image_peak_bytes = 0
        self.request_limit = request_max_bytes(adapter_id)

    async def prepare(self, transcript: list[CanonicalTranscriptItem], tools: object = None) -> None:
        global _encoding_active
        image_parts = [p for i in transcript for p in user_parts(i) if isinstance(p, ImagePart)]
        if not image_parts:
            return
        if self.reader is None:
            raise AppError(500, "CONFIG_ERROR", "图片请求缺少附件读取服务")
        max_images, max_dimension = adapter_image_limits(self.adapter_id)
        # Count every content block in the complete context, including history.
        # Repeated references still consume the upstream image count limit.
        if max_images > 0 and len(image_parts) > max_images:
            raise AppError(413, "VISION_CONTEXT_LIMIT", "历史上下文中的图片数量超过协议预算，请缩短上下文后重试")
        metadata: dict[int, AttachmentMetadata] = {}
        for part in image_parts:
            if part.attachment_id not in metadata:
                row = await self.reader.metadata(part.attachment_id)
                metadata[part.attachment_id] = row
            row = metadata[part.attachment_id]
            status = getattr(row.status, "value", row.status)
            if status != "ready" or row.media_type != part.media_type or row.size_bytes <= 0:
                raise AppError(409, "ATTACHMENT_INVALID", "图片附件状态或内容已发生变化")
            if max_dimension > 0 and max(row.width, row.height) > max_dimension:
                raise AppError(413, "VISION_CONTEXT_LIMIT", "图片尺寸超过协议预算，请调整图片或切换服务商后重试")
        encoded_bytes = sum(4 * ((metadata[p.attachment_id].size_bytes + 2) // 3) + 256 for p in image_parts)
        text_bytes = sum(_json_upper_bound((i.text, i.arguments, i.result, i.tool_name)) for i in transcript)
        text_bytes += sum(_json_upper_bound(p.text) for i in transcript for p in user_parts(i) if isinstance(p, TextPart))
        estimated_request = encoded_bytes + text_bytes + _json_upper_bound(tools) + 4096 * len(transcript)
        if estimated_request > self.request_limit:
            raise AppError(413, "VISION_REQUEST_TOO_LARGE", "图片及历史内容超过模型请求预算，请缩短上下文后重试")
        raw_bytes = sum(row.size_bytes for row in metadata.values())
        # Two raw copies, three base64/data-URL copies, four request copies
        # (Unicode JSON, UTF-8 and HTTP buffers), plus 1 MiB allocator margin.
        self.image_peak_bytes = 2 * raw_bytes + 3 * encoded_bytes + 1024 * 1024
        self.lease = get_vision_buffer_budget().reserve(self.image_peak_bytes + 4 * estimated_request)
        with _encoding_lock:
            limit = getattr(get_settings(), "vision_encoding_concurrency", 1)
            if _encoding_active >= limit:
                raise AppError(429, "VISION_ENCODING_BUSY", "图片编码服务忙，请稍后重试")
            _encoding_active += 1
        try:
            for attachment_id, row in metadata.items():
                # Reader performs bounded asynchronous disk I/O; encoding is
                # synchronous so cancellation cannot outlive buffer ownership.
                raw = await self.reader.read(attachment_id)
                if len(raw) != row.size_bytes:
                    raise AppError(409, "ATTACHMENT_INVALID", "图片附件大小与记录不一致")
                self.images[attachment_id] = base64.b64encode(raw).decode("ascii")
                del raw
        finally:
            with _encoding_lock:
                _encoding_active -= 1

    def check_request_data(self, data: object) -> None:
        estimate = _json_upper_bound(data) + 16384
        if self.lease is not None:
            self.lease.ensure(self.image_peak_bytes + 4 * estimate)

    def check_payload(self, payload: dict[str, object]) -> None:
        self.check_request_data(payload)
        # Same serialization options as httpx; iterencode avoids another full
        # retained request copy while counting actual UTF-8 bytes.
        total = 0
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        for chunk in encoder.iterencode(payload):
            total += len(chunk.encode("utf-8"))
            if total > self.request_limit:
                raise AppError(413, "VISION_REQUEST_TOO_LARGE", "图片及历史内容超过模型请求预算，请缩短上下文后重试")

    def close(self) -> None:
        self.images.clear()
        if self.lease is not None:
            self.lease.close()


def provider_user_content(item: CanonicalTranscriptItem, adapter_id: str, images: dict[int, str]) -> object:
    parts = user_parts(item)
    if not any(isinstance(p, ImagePart) for p in parts):
        return "".join(p.text for p in parts if isinstance(p, TextPart))
    blocks: list[dict[str, object]] = []
    for part in parts:
        if isinstance(part, TextPart):
            blocks.append({"type": "input_text" if adapter_id == "openai_responses" else "text", "text": part.text})
        else:
            encoded = images[part.attachment_id]
            if adapter_id == "anthropic_messages":
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": encoded}})
            else:
                url = f"data:{part.media_type};base64,{encoded}"
                blocks.append({"type": "input_image", "image_url": url} if adapter_id == "openai_responses"
                              else {"type": "image_url", "image_url": {"url": url}})
    return blocks


def vision_request(adapter_id: str):
    """Release only after the provider loop and its generator actually exit."""
    def decorate(function):
        if inspect.isasyncgenfunction(function):
            @wraps(function)
            async def stream_wrapper(*args, **kwargs):
                context = VisionRequestContext(adapter_id, kwargs.get("attachment_reader"))
                iterator = None
                try:
                    await context.prepare(kwargs["transcript"], kwargs.get("tools"))
                    kwargs["vision_context"] = context
                    iterator = function(*args, **kwargs)
                    async for event in iterator:
                        yield event
                finally:
                    try:
                        if iterator is not None:
                            await iterator.aclose()
                    finally:
                        context.close()
            return stream_wrapper
        @wraps(function)
        async def reply_wrapper(*args, **kwargs):
            context = VisionRequestContext(adapter_id, kwargs.get("attachment_reader"))
            try:
                await context.prepare(kwargs["transcript"], kwargs.get("tools"))
                kwargs["vision_context"] = context
                return await function(*args, **kwargs)
            finally:
                context.close()
        return reply_wrapper
    return decorate
