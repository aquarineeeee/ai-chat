from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.core.config import get_settings


logger = logging.getLogger(__name__)
MEMORY_CONTEXT_PREFIX = "以下是长期记忆检索结果，仅供参考，可能相关，但不保证是当前最新事实。"
MCP_PROTOCOL_VERSION = "2025-11-25"
NO_MEMORY_RESULTS = {"未找到相关记忆", "未找到相关记忆。"}


def _extract_text(result: Any) -> str | None:
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts)

    structured = result.get("structuredContent") if isinstance(result, dict) else None
    if structured:
        if isinstance(structured, str):
            return structured.strip() or None
        return json.dumps(structured, ensure_ascii=False)

    return None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _normalize_memory_text(text: str) -> str:
    cleaned_chars: list[str] = []
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char in {"\n", "\t"} or ord(char) >= 32:
            cleaned_chars.append(char)
    return "".join(cleaned_chars).strip()


def _preview(text: str, limit: int = 120) -> str:
    return _truncate(text.replace("\n", "\\n"), limit)


def _is_no_memory_result(text: str) -> bool:
    return text.strip() in NO_MEMORY_RESULTS


def _http_response_summary(response: httpx.Response, *, limit: int = 500) -> str:
    content_type = response.headers.get("content-type", "")
    return (
        f"status={response.status_code}, "
        f"content_type={content_type!r}, "
        f"body_preview={_preview(response.text, limit)!r}"
    )


def _exception_summary(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        parts: list[str] = []
        for item in exc.exceptions:
            summary = _exception_summary(item)
            if summary:
                parts.append(summary)
        return " | ".join(parts)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__}: {exc} ({_http_response_summary(exc.response)})"
    return f"{type(exc).__name__}: {exc}"


def _sse_message_payload(body: str) -> dict[str, Any]:
    for block in body.split("\n\n"):
        data_lines = [line[5:] for line in block.splitlines() if line.startswith("data: ")]
        if not data_lines:
            continue
        payload = "\n".join(data_lines).strip()
        if not payload:
            continue
        return json.loads(payload)
    raise ValueError("MCP SSE response did not contain a data payload")


async def _call_mcp_tool(tool_name: str, arguments: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    settings = get_settings()
    effective_timeout = timeout if timeout is not None else settings.memory_timeout_seconds
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    session_id: str | None = None

    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    async with httpx.AsyncClient(timeout=effective_timeout, transport=transport) as client:
        try:
            initialize_response = await client.post(
                settings.memory_mcp_url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "ai-chat", "version": "0.1.0"},
                    },
                },
            )
            initialize_response.raise_for_status()
            session_id = initialize_response.headers.get("mcp-session-id")
            if not session_id:
                raise RuntimeError("MCP initialize response did not include mcp-session-id")

            session_headers = {
                **headers,
                "mcp-session-id": session_id,
                "mcp-protocol-version": MCP_PROTOCOL_VERSION,
            }

            tool_response = await client.post(
                settings.memory_mcp_url,
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                },
            )
            tool_response.raise_for_status()

            payload = _sse_message_payload(tool_response.text)
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("MCP tool response did not include a result object")
            return result
        finally:
            if session_id:
                try:
                    await client.delete(
                        settings.memory_mcp_url,
                        headers={
                            **headers,
                            "mcp-session-id": session_id,
                            "mcp-protocol-version": MCP_PROTOCOL_VERSION,
                        },
                    )
                except Exception:
                    logger.debug("Failed to terminate MCP session %s", session_id, exc_info=True)


async def search_memory(
    *,
    query: str = "",
    max_tokens: int = 1500,
    domain: str = "",
    valence: float = -1,
    arousal: float = -1,
    max_results: int = 8,
    importance_min: int = -1,
) -> str | None:
    settings = get_settings()
    cleaned_query = _normalize_memory_text(query)
    cleaned_domain = _normalize_memory_text(domain)
    if not settings.memory_enabled:
        return None

    try:
        async with asyncio.timeout(settings.memory_timeout_seconds):
            result = await _call_mcp_tool(
                "breath",
                {
                    "query": cleaned_query,
                    "max_tokens": max_tokens,
                    "domain": cleaned_domain,
                    "valence": valence,
                    "arousal": arousal,
                    "max_results": max_results,
                    "importance_min": importance_min,
                },
            )
    except Exception as exc:
        logger.exception(
            "Memory search failed for %s (%s, query_len=%s, query_preview=%r)",
            settings.memory_mcp_url,
            _exception_summary(exc),
            len(cleaned_query),
            _preview(cleaned_query),
        )
        return None

    text = _extract_text(result)
    if not text or _is_no_memory_result(text):
        return None
    return f"{MEMORY_CONTEXT_PREFIX}\n{text[: settings.memory_max_context_chars]}".strip()


def _memory_pulse_result(*, include_archive: bool) -> str:
    lines = ["Memory status fetched."]
    if include_archive:
        lines.append("include_archive=true")
    return "\n".join(lines)


async def pulse_memory(*, include_archive: bool = False) -> str:
    settings = get_settings()
    if not settings.memory_enabled:
        return "Memory store disabled; skipped pulse."

    payload = {
        "include_archive": include_archive,
    }
    try:
        result = await _call_mcp_tool("pulse", payload, timeout=settings.memory_timeout_seconds)
        text = _extract_text(result)
        if text:
            return text
        return _memory_pulse_result(include_archive=include_archive)
    except Exception as exc:
        logger.error(
            "Memory pulse failed for %s (%s, include_archive=%s)",
            settings.memory_mcp_url,
            _exception_summary(exc),
            include_archive,
            exc_info=exc,
        )
        raise


def _memory_write_result(
    *,
    content: str,
    tags: str,
    importance: int,
    pinned: bool,
    feel: bool,
    source_bucket: str,
    valence: float,
    arousal: float,
) -> str:
    lines = [
        "记忆写入成功。",
        f"正文：{_preview(content, 200)}",
    ]
    if tags:
        lines.append(f"标签：{tags}")
    if importance != 5:
        lines.append(f"重要度：{importance}")
    if pinned:
        lines.append("钉选：true")
    if feel:
        lines.append("feel：true")
    if source_bucket:
        lines.append(f"源记忆桶：{source_bucket}")
    if 0 <= valence <= 1:
        lines.append(f"valence：{valence}")
    if 0 <= arousal <= 1:
        lines.append(f"arousal：{arousal}")
    return "\n".join(lines)


def _memory_update_result(
    *,
    bucket_id: str,
    name: str,
    domain: str,
    valence: float,
    arousal: float,
    importance: int,
    tags: str,
    resolved: int,
    pinned: int,
    digested: int,
    content: str,
    delete: bool,
) -> str:
    lines = [
        "记忆更新成功。",
        f"bucket_id：{bucket_id}",
    ]
    if delete:
        lines.append("delete：true")
        return "\n".join(lines)
    if name:
        lines.append(f"name：{name}")
    if domain:
        lines.append(f"domain：{domain}")
    if 0 <= valence <= 1:
        lines.append(f"valence：{valence}")
    if 0 <= arousal <= 1:
        lines.append(f"arousal：{arousal}")
    if importance >= 0:
        lines.append(f"importance：{importance}")
    if tags:
        lines.append(f"tags：{tags}")
    if resolved >= 0:
        lines.append(f"resolved：{resolved}")
    if pinned >= 0:
        lines.append(f"pinned：{pinned}")
    if digested >= 0:
        lines.append(f"digested：{digested}")
    if content:
        lines.append(f"content：{_preview(content, 200)}")
    return "\n".join(lines)


async def write_memory(
    *,
    content: str,
    tags: str = "",
    importance: int = 5,
    pinned: bool = False,
    feel: bool = False,
    source_bucket: str = "",
    valence: float = -1,
    arousal: float = -1,
) -> str:
    settings = get_settings()
    if not settings.memory_enabled:
        return "记忆库未启用，未写入记忆。"

    cleaned_content = _normalize_memory_text(content)
    if not cleaned_content:
        return "记忆正文为空，未写入记忆。"

    cleaned_tags = _normalize_memory_text(tags)
    cleaned_source_bucket = _normalize_memory_text(source_bucket)
    payload = {
        "content": _truncate(cleaned_content, settings.memory_write_max_chars),
        "tags": cleaned_tags,
        "importance": importance,
        "pinned": pinned,
        "feel": feel,
        "source_bucket": cleaned_source_bucket,
        "valence": valence,
        "arousal": arousal,
    }
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            result = await _call_mcp_tool("hold", payload, timeout=settings.memory_write_timeout_seconds)
            text = _extract_text(result)
            if text:
                return text
            return _memory_write_result(
                content=payload["content"],
                tags=cleaned_tags,
                importance=importance,
                pinned=pinned,
                feel=feel,
                source_bucket=cleaned_source_bucket,
                valence=valence,
                arousal=arousal,
            )
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(0.2)
                continue

    if last_exc is not None:
        logger.error(
            "Memory write failed for %s (%s, content_len=%s, content_preview=%r)",
            settings.memory_mcp_url,
            _exception_summary(last_exc),
            len(payload["content"]),
            _preview(payload["content"]),
            exc_info=last_exc,
        )
        raise last_exc


def _memory_grow_result(*, content: str) -> str:
    return "\n".join(
        [
            "Memory import succeeded.",
            f"content={_preview(content, 200)}",
        ]
    )


async def grow_memory(*, content: str) -> str:
    settings = get_settings()
    if not settings.memory_enabled:
        return "Memory store disabled; skipped import."

    cleaned_content = _normalize_memory_text(content)
    if not cleaned_content:
        return "Memory content is empty; skipped import."

    payload = {
        "content": _truncate(cleaned_content, settings.memory_write_max_chars),
    }
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            result = await _call_mcp_tool("grow", payload, timeout=settings.memory_write_timeout_seconds)
            text = _extract_text(result)
            if text:
                return text
            return _memory_grow_result(content=payload["content"])
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(0.2)
                continue

    if last_exc is not None:
        logger.error(
            "Memory grow failed for %s (%s, content_len=%s, content_preview=%r)",
            settings.memory_mcp_url,
            _exception_summary(last_exc),
            len(payload["content"]),
            _preview(payload["content"]),
            exc_info=last_exc,
        )
        raise last_exc


async def update_memory(
    *,
    bucket_id: str,
    name: str = "",
    domain: str = "",
    valence: float = -1,
    arousal: float = -1,
    importance: int = -1,
    tags: str = "",
    resolved: int = -1,
    pinned: int = -1,
    digested: int = -1,
    content: str = "",
    delete: bool = False,
) -> str:
    settings = get_settings()
    if not settings.memory_enabled:
        return "记忆库未启用，未更新记忆。"

    cleaned_bucket_id = _normalize_memory_text(bucket_id)
    if not cleaned_bucket_id:
        return "bucket_id 不能为空"

    cleaned_name = _normalize_memory_text(name)
    cleaned_domain = _normalize_memory_text(domain)
    cleaned_tags = _normalize_memory_text(tags)
    cleaned_content = _normalize_memory_text(content)
    payload = {
        "bucket_id": cleaned_bucket_id,
        "name": cleaned_name,
        "domain": cleaned_domain,
        "valence": valence,
        "arousal": arousal,
        "importance": importance,
        "tags": cleaned_tags,
        "resolved": resolved,
        "pinned": pinned,
        "digested": digested,
        "content": _truncate(cleaned_content, settings.memory_write_max_chars),
        "delete": delete,
    }
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            result = await _call_mcp_tool("trace", payload, timeout=settings.memory_write_timeout_seconds)
            text = _extract_text(result)
            if text:
                return text
            return _memory_update_result(
                bucket_id=cleaned_bucket_id,
                name=cleaned_name,
                domain=cleaned_domain,
                valence=valence,
                arousal=arousal,
                importance=importance,
                tags=cleaned_tags,
                resolved=resolved,
                pinned=pinned,
                digested=digested,
                content=payload["content"],
                delete=delete,
            )
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(0.2)
                continue

    if last_exc is not None:
        logger.error(
            "Memory update failed for %s (%s, bucket_id=%r, delete=%s, content_preview=%r)",
            settings.memory_mcp_url,
            _exception_summary(last_exc),
            cleaned_bucket_id,
            delete,
            _preview(payload["content"]),
            exc_info=last_exc,
        )
        raise last_exc
